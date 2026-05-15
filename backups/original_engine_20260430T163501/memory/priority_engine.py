"""
Layered configuration resolution with conflict logging.
"""
from __future__ import annotations

import copy
import json
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import yaml
from .file_write_guard import atomic_write_json, atomic_write_text


class ConfigPriorityEngine:
    """Resolve config layers with deterministic precedence and conflict logs."""

    def __init__(self, workspace_dir: Path, skill_root: Path, default_config: Dict[str, Any]):
        self.workspace_dir = workspace_dir
        self.skill_root = skill_root
        self.default_config = copy.deepcopy(default_config)
        self.memory_dir = workspace_dir / "memory"
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.memory_dir / "config_resolution_log.json"
        self.gitignore_file = self.workspace_dir / ".gitignore"

        layer_settings = self.default_config.get("config_layers", {})
        self.protected_paths = set(layer_settings.get("protected_paths", []))
        self.layer_files = {
            "admin": self.skill_root / "references" / "admin_defaults.yaml",
            "user": self.workspace_dir / "config.yaml",
            "project": self.workspace_dir / "config.project.yaml",
            "local": self.workspace_dir / "config.local.yaml",
        }

    def _load_yaml(self, path: Path) -> Dict[str, Any]:
        if not path.exists():
            return {}
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return yaml.safe_load(handle) or {}
        except Exception:
            return {}

    def _flatten(self, value: Dict[str, Any], prefix: str = "") -> Dict[str, Any]:
        flattened: Dict[str, Any] = {}
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(item, dict):
                flattened.update(self._flatten(item, path))
            else:
                flattened[path] = item
        return flattened

    def _set_path(self, target: Dict[str, Any], path: str, value: Any) -> None:
        parts = path.split(".")
        cursor = target
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[parts[-1]] = value

    def _append_gitignore(self, lines: Iterable[str]) -> None:
        existing: List[str] = []
        if self.gitignore_file.exists():
            existing = self.gitignore_file.read_text(encoding="utf-8").splitlines()
        additions = [line for line in lines if line not in existing]
        if not additions:
            return
        payload = existing + additions
        atomic_write_text(self.gitignore_file, "\n".join(payload).rstrip() + "\n", encoding="utf-8")

    def ensure_local_overlay_support(self) -> None:
        self._append_gitignore(
            [
                "config.local.yaml",
                "runtime.local.env",
                "memory/config_resolution_log.json",
                "memory/auto_memory_log.json",
                "memory/backups/",
                "memory/restore_drill/",
                "memory/cleanup_snapshots/",
                "backup_*/",
                "cleanup_backup_*/",
                "session_fix_backup_*/",
                "memory/whoosh_index/",
                "memory/*.archived.log",
                "memory/*.bak",
            ]
        )

    def _load_log(self) -> Dict[str, Any]:
        if self.log_file.exists():
            try:
                return json.loads(self.log_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"entries": []}

    def _save_log(self, entry: Dict[str, Any]) -> None:
        state = self._load_log()
        entries = state.get("entries", [])
        payload_hash = hashlib.sha1(
            json.dumps(
                {
                    "applied_layers": entry.get("applied_layers", []),
                    "conflicts": entry.get("conflicts", []),
                    "blocked_overrides": entry.get("blocked_overrides", []),
                },
                sort_keys=True,
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        # Write only when meaningfully changed, or after a cooldown to keep heartbeat visibility.
        should_append = True
        if entries:
            last = entries[-1]
            last_hash = str(last.get("payload_hash", ""))
            if last_hash == payload_hash:
                try:
                    ts = datetime.fromisoformat(str(last.get("timestamp", "")))
                    if (datetime.now() - ts).total_seconds() < 600:
                        should_append = False
                except Exception:
                    pass
        if not should_append:
            return
        entry["payload_hash"] = payload_hash
        entries.append(entry)
        state["entries"] = entries[-100:]
        try:
            atomic_write_json(self.log_file, state, ensure_ascii=False, indent=2)
        except Exception:
            # Config resolution should not fail because diagnostics log is not writable.
            pass

    def resolve(self) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        self.ensure_local_overlay_support()

        resolved = copy.deepcopy(self.default_config)
        assignments = {path: ("default", value) for path, value in self._flatten(self.default_config).items()}
        conflicts: List[Dict[str, Any]] = []
        blocked: List[Dict[str, Any]] = []
        applied_layers: List[Dict[str, Any]] = []

        for layer_name in ("admin", "user", "project", "local"):
            path = self.layer_files[layer_name]
            data = self._load_yaml(path)
            if not data:
                continue
            applied_layers.append({"layer": layer_name, "path": str(path)})
            for dotted_path, value in self._flatten(data).items():
                previous_layer, previous_value = assignments.get(dotted_path, ("default", None))
                if previous_value == value:
                    assignments[dotted_path] = (layer_name, value)
                    self._set_path(resolved, dotted_path, value)
                    continue
                if dotted_path in self.protected_paths and previous_layer == "admin":
                    blocked.append(
                        {
                            "path": dotted_path,
                            "attempted_layer": layer_name,
                            "winning_layer": previous_layer,
                            "winning_value": previous_value,
                            "blocked_value": value,
                        }
                    )
                    continue
                if previous_layer != "default":
                    conflicts.append(
                        {
                            "path": dotted_path,
                            "previous_layer": previous_layer,
                            "previous_value": previous_value,
                            "winning_layer": layer_name,
                            "winning_value": value,
                        }
                    )
                assignments[dotted_path] = (layer_name, value)
                self._set_path(resolved, dotted_path, value)

        report = {
            "timestamp": datetime.now().isoformat(),
            "applied_layers": applied_layers,
            "protected_paths": sorted(self.protected_paths),
            "conflicts": conflicts,
            "blocked_overrides": blocked,
            "layer_files": {name: str(path) for name, path in self.layer_files.items()},
        }
        self._save_log(report)
        return resolved, report

    def get_report(self) -> Dict[str, Any]:
        return self._load_log()

    def _write_yaml(self, path: Path, data: Dict[str, Any]) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            yaml.safe_dump(data, handle, allow_unicode=True, sort_keys=False)
        return path

    def write_project_override(self, data: Dict[str, Any]) -> Path:
        return self._write_yaml(self.layer_files["project"], data)

    def write_local_override(self, data: Dict[str, Any]) -> Path:
        return self._write_yaml(self.layer_files["local"], data)

    def clear_local_override(self) -> bool:
        local_path = self.layer_files["local"]
        if not local_path.exists():
            return False
        local_path.unlink()
        return True
