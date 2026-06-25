"""Project scope and registry helpers for absorb project-memory."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...paths import get_ms8_home


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def project_memory_root() -> Path:
    return get_ms8_home() / "absorb" / "project_memory"


def registry_path() -> Path:
    return project_memory_root() / "config.json"


def _default_registry() -> dict[str, Any]:
    return {"version": 1, "projects": {}}


def _sanitize_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip("-")
    return cleaned or "project"


def load_registry() -> dict[str, Any]:
    path = registry_path()
    if not path.exists():
        return _default_registry()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return _default_registry()


def save_registry(payload: dict[str, Any]) -> None:
    root = project_memory_root()
    root.mkdir(parents=True, exist_ok=True)
    registry_path().write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_project_name(project_dir: str | Path, name: str | None = None) -> str:
    if name:
        return _sanitize_name(name)
    return _sanitize_name(Path(project_dir).expanduser().resolve().name)


def project_dir_paths(name: str) -> dict[str, Path]:
    base = project_memory_root() / name
    return {
        "base": base,
        "db_path": base / "project.sqlite",
        "whoosh_dir": base / "whoosh",
        "output_dir": base / "output",
        "index_state_path": base / "index_state.json",
        "watch_state_path": base / "watch_state.json",
        "build_state_path": base / "build_state.json",
    }


def default_index_state() -> dict[str, Any]:
    return {
        "version": 1,
        "status": "missing",
        "backend": "whoosh",
        "content_db_ready": False,
        "search_index_ready": False,
        "changed_files_pending": 0,
        "changed_paths": [],
        "deleted_paths": [],
        "last_index_at": "",
        "last_full_rebuild_at": "",
        "last_error": "",
    }


def load_index_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return default_index_state()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            base = default_index_state()
            base.update(payload)
            return base
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    broken = default_index_state()
    broken["status"] = "broken"
    broken["last_error"] = "invalid_index_state_json"
    return broken


def save_index_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def default_watch_state() -> dict[str, Any]:
    return {
        "version": 1,
        "running": False,
        "pid": 0,
        "started_at": "",
        "heartbeat_at": "",
        "stopped_at": "",
        "cycles_run": 0,
        "last_status": "",
        "last_error": "",
        "last_cycle": {},
    }


def default_build_state() -> dict[str, Any]:
    return {
        "version": 1,
        "snapshot_hash": "",
        "last_build_at": "",
        "last_error": "",
        "files": {},
    }


def load_watch_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return default_watch_state()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            base = default_watch_state()
            base.update(payload)
            return base
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    broken = default_watch_state()
    broken["last_error"] = "invalid_watch_state_json"
    return broken


def load_build_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return default_build_state()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            base = default_build_state()
            base.update(payload)
            files = base.get("files", {})
            if not isinstance(files, dict):
                base["files"] = {}
            return base
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    broken = default_build_state()
    broken["last_error"] = "invalid_build_state_json"
    return broken


def save_watch_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def save_build_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def mark_index_stale(
    path: Path,
    *,
    content_db_ready: bool,
    changed_files_pending: int,
    changed_paths: list[str] | None = None,
    deleted_paths: list[str] | None = None,
    reason: str = "",
) -> dict[str, Any]:
    payload = load_index_state(path)
    payload.update(
        {
            "status": "stale",
            "backend": "whoosh",
            "content_db_ready": bool(content_db_ready),
            "search_index_ready": False,
            "changed_files_pending": int(max(0, changed_files_pending)),
            "changed_paths": list(changed_paths or []),
            "deleted_paths": list(deleted_paths or []),
            "last_error": str(reason or ""),
        }
    )
    save_index_state(path, payload)
    return payload


def mark_index_ready(
    path: Path,
    *,
    full_rebuild: bool,
    changed_files_pending: int = 0,
) -> dict[str, Any]:
    payload = load_index_state(path)
    now = _now()
    payload.update(
        {
            "status": "ready",
            "backend": "whoosh",
            "content_db_ready": True,
            "search_index_ready": True,
            "changed_files_pending": int(max(0, changed_files_pending)),
            "changed_paths": [],
            "deleted_paths": [],
            "last_index_at": now,
            "last_error": "",
        }
    )
    if full_rebuild:
        payload["last_full_rebuild_at"] = now
    save_index_state(path, payload)
    return payload


def mark_index_degraded(
    path: Path,
    *,
    changed_files_pending: int,
    error: str,
    changed_paths: list[str] | None = None,
    deleted_paths: list[str] | None = None,
) -> dict[str, Any]:
    payload = load_index_state(path)
    payload.update(
        {
            "status": "degraded",
            "backend": "whoosh",
            "content_db_ready": True,
            "search_index_ready": False,
            "changed_files_pending": int(max(0, changed_files_pending)),
            "changed_paths": list(changed_paths or payload.get("changed_paths", []) or []),
            "deleted_paths": list(deleted_paths or payload.get("deleted_paths", []) or []),
            "last_error": str(error or "index_build_failed"),
        }
    )
    save_index_state(path, payload)
    return payload


def init_project(project_dir: str | Path, name: str | None = None) -> dict[str, Any]:
    root = Path(project_dir).expanduser().resolve()
    project_name = resolve_project_name(root, name)
    reg = load_registry()
    projects = reg.setdefault("projects", {})
    paths = project_dir_paths(project_name)
    paths["base"].mkdir(parents=True, exist_ok=True)
    paths["whoosh_dir"].mkdir(parents=True, exist_ok=True)
    paths["output_dir"].mkdir(parents=True, exist_ok=True)
    if not paths["index_state_path"].exists():
        save_index_state(paths["index_state_path"], default_index_state())
    now = _now()
    existing = projects.get(project_name, {})
    projects[project_name] = {
        "name": project_name,
        "root": str(root),
        "created_at": existing.get("created_at", now),
        "updated_at": now,
        "file_count": int(existing.get("file_count", 0) or 0),
        "chunk_count": int(existing.get("chunk_count", 0) or 0),
        "last_scan_at": existing.get("last_scan_at", ""),
        "auto_write_main_memory": bool(existing.get("auto_write_main_memory", False)),
        "last_summary_hash": str(existing.get("last_summary_hash", "") or ""),
        "last_summary_record_id": str(existing.get("last_summary_record_id", "") or ""),
        "last_summary_submitted_at": str(existing.get("last_summary_submitted_at", "") or ""),
    }
    save_registry(reg)
    return {
        "ok": True,
        "name": project_name,
        "root": str(root),
        "db_path": str(paths["db_path"]),
        "output_dir": str(paths["output_dir"]),
        "next_actions": [f"ms8 absorb project-memory scan --name {project_name}"],
    }


def get_project(name: str | None = None) -> dict[str, Any]:
    reg = load_registry()
    projects = reg.get("projects", {})
    if not isinstance(projects, dict) or not projects:
        return {"ok": False, "error": "no_project_registered", "next_actions": ["ms8 absorb project-memory init <project_dir>"]}
    if name:
        item = projects.get(name)
        if not isinstance(item, dict):
            return {"ok": False, "error": "project_not_found", "name": name, "available_projects": sorted(projects)}
        return {"ok": True, "project": item}
    if len(projects) == 1:
        return {"ok": True, "project": next(iter(projects.values()))}
    latest = sorted(
        (v for v in projects.values() if isinstance(v, dict)),
        key=lambda item: str(item.get("updated_at", "")),
        reverse=True,
    )
    if latest:
        return {"ok": True, "project": latest[0], "selected_implicitly": True}
    return {"ok": False, "error": "project_not_found"}


def list_projects() -> list[dict[str, Any]]:
    reg = load_registry()
    projects = reg.get("projects", {})
    if not isinstance(projects, dict):
        return []
    items = [dict(value) for value in projects.values() if isinstance(value, dict)]
    return sorted(items, key=lambda item: str(item.get("name", "")))


def update_project_stats(name: str, *, file_count: int, chunk_count: int, last_scan_at: str | None = None) -> None:
    reg = load_registry()
    projects = reg.setdefault("projects", {})
    item = projects.get(name)
    if not isinstance(item, dict):
        return
    item["file_count"] = int(file_count)
    item["chunk_count"] = int(chunk_count)
    if last_scan_at is not None:
        item["last_scan_at"] = last_scan_at
    item["updated_at"] = _now()
    save_registry(reg)


def update_project_fields(name: str, **fields: Any) -> dict[str, Any]:
    reg = load_registry()
    projects = reg.setdefault("projects", {})
    item = projects.get(name)
    if not isinstance(item, dict):
        return {"ok": False, "error": "project_not_found", "name": name}
    item.update(fields)
    item["updated_at"] = _now()
    save_registry(reg)
    return {"ok": True, "project": item}


def set_auto_write_main_memory(name: str, enabled: bool) -> dict[str, Any]:
    updated = update_project_fields(name, auto_write_main_memory=bool(enabled))
    if not bool(updated.get("ok", False)):
        return updated
    project = dict(updated.get("project", {}))
    return {
        "ok": True,
        "name": name,
        "auto_write_main_memory": bool(project.get("auto_write_main_memory", False)),
    }
