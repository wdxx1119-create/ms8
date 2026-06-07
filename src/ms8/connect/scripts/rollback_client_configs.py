from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .client_config import SERVER_NAME, target_paths

logger = logging.getLogger(__name__)


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        logger.debug("Failed to read JSON %s: %s", path, exc)
        return {}
    return obj if isinstance(obj, dict) else {}


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def run(
    target: str = "all",
    *,
    dry_run: bool = False,
    force_delete_full_config: bool = False,
) -> dict[str, object]:
    targets = list(target_paths(target).values())
    removed: list[str] = []
    modified: list[str] = []
    failed: list[dict[str, str]] = []
    backups: list[str] = []
    preview: list[dict[str, object]] = []
    for p in targets:
        try:
            if not p.exists():
                preview.append({"path": str(p), "action": "skip_missing"})
                continue
            payload = _read_json(p)
            servers = payload.get("mcpServers", {}) if isinstance(payload.get("mcpServers", {}), dict) else {}
            has_ms8 = SERVER_NAME in servers
            other_keys = [k for k in servers.keys() if k != SERVER_NAME]
            if force_delete_full_config:
                preview.append({"path": str(p), "action": "delete_full_config", "had_ms8": has_ms8})
                if dry_run:
                    continue
                p.unlink(missing_ok=True)
                removed.append(str(p))
                continue
            if not has_ms8:
                preview.append({"path": str(p), "action": "skip_no_ms8_entry", "other_server_count": len(other_keys)})
                continue
            new_payload = dict(payload)
            new_servers = dict(servers)
            new_servers.pop(SERVER_NAME, None)
            new_payload["mcpServers"] = new_servers
            preview.append(
                {
                    "path": str(p),
                    "action": "remove_ms8_entry",
                    "other_server_count_after": len(new_servers),
                }
            )
            if dry_run:
                continue
            backup_path = p.with_suffix(p.suffix + f".ms8bak.{_stamp()}")
            backup_path.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
            backups.append(str(backup_path))
            try:
                _write_json(p, new_payload)
                modified.append(str(p))
            except OSError as exc:
                logger.warning("Rollback write failed, attempting restore from backup: %s", exc)
                # best-effort rollback
                p.write_text(backup_path.read_text(encoding="utf-8"), encoding="utf-8")
                raise
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            failed.append({"path": str(p), "error": str(exc)})
    return {
        "ok": len(failed) == 0,
        "target": target,
        "dry_run": bool(dry_run),
        "force_delete_full_config": bool(force_delete_full_config),
        "removed": removed,
        "modified": modified,
        "backups": backups,
        "preview": preview,
        "failed": failed,
    }


def main() -> dict:
    return run()


if __name__ == "__main__":
    print(main())
