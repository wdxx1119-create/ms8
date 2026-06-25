"""Lightweight runtime filesystem and read-only health helpers."""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import get_config_dir, get_data_dir, get_health_dir, get_log_dir, get_ms8_home
from .record_policy import repair_scope_flags, validate_file_and_quarantine

logger = logging.getLogger(__name__)


def get_runtime_dir() -> Path:
    return get_ms8_home()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_memories_path(root: Path, data: Path) -> Path:
    canonical = root / "memory" / "auto_memory_records.jsonl"
    legacy = data / "memories.jsonl"
    if canonical.exists():
        return canonical
    if legacy.exists():
        return legacy
    return canonical


def ensure_runtime_dirs() -> dict[str, Path]:
    root = get_runtime_dir()
    data = get_data_dir()
    config = get_config_dir()
    logs = get_log_dir()
    health = get_health_dir()
    backups = root / "backups"
    for path in (root, data, config, backups, logs, health):
        path.mkdir(parents=True, exist_ok=True)

    memories = _default_memories_path(root, data)
    memories.parent.mkdir(parents=True, exist_ok=True)
    memories.touch(exist_ok=True)

    activity = health / "activity.json"
    compression_state = root / "memory" / "compression_state.json"
    maintenance_window = health / "maintenance_window.json"
    quarantine_file = root / "memory" / "noncanonical_quarantine.jsonl"
    quarantine_file.parent.mkdir(parents=True, exist_ok=True)
    quarantine_file.touch(exist_ok=True)

    if not compression_state.exists():
        compression_state.write_text(
            json.dumps(
                {
                    "status": "initialized",
                    "last_run_at": "",
                    "last_result": {},
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    config_file = root / "config.json"
    if not config_file.exists():
        config_file.write_text(
            json.dumps(
                {
                    "governance_risk": {
                        "red": {
                            "schema_invalid_count_gt": 0,
                            "fallback_write_count_gt": 5,
                            "noncanonical_records_gt": 0,
                        },
                        "yellow": {
                            "fallback_write_count_gt": 0,
                            "pending_review_gt": 5,
                            "duplicate_groups_gt": 5,
                        },
                    },
                    "dedupe": {
                        "enabled": True,
                        "collapse_superseded": True,
                        "archive_file": "memory/archive/superseded_duplicates_auto.jsonl",
                    },
                    "governance_slo": {
                        "authority": "v2_preview",
                        "v2_min_eligible_events": 30,
                    },
                    "labs": {
                        "enabled": False,
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    try:
        repair_scope_flags(memories)
    except OSError as exc:
        logger.debug("Failed to repair scope flags during runtime dir init: %s", exc)
    try:
        validate_file_and_quarantine(memories, quarantine_file)
    except OSError as exc:
        logger.debug("Failed to validate memory file during runtime dir init: %s", exc)

    return {
        "root": root,
        "data": data,
        "backups": backups,
        "logs": logs,
        "config": config,
        "health": health,
        "memories": memories,
        "activity": activity,
        "compression_state": compression_state,
        "quarantine": quarantine_file,
        "maintenance_window": maintenance_window,
        "config_file": config_file,
    }


def read_memories() -> list[dict[str, Any]]:
    memories = ensure_runtime_dirs()["memories"]
    rows: list[dict[str, Any]] = []
    for line in memories.read_text(encoding="utf-8", errors="ignore").splitlines():
        raw = line.strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.debug("Failed to parse memory row in runtime health reader: %s", exc)
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def count_memories() -> int:
    return len(read_memories())


def last_write_time() -> str | None:
    memories = ensure_runtime_dirs()["memories"]
    if not memories.exists():
        return None
    try:
        ts = memories.stat().st_mtime
    except OSError as exc:
        logger.debug("Failed to stat memories file for last write time: %s", exc)
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def backup_memories(tag: str = "manual") -> dict[str, Any]:
    paths = ensure_runtime_dirs()
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_file = paths["backups"] / f"memories-{tag}-{ts}.jsonl"
    shutil.copy2(paths["memories"], backup_file)
    return {"ok": True, "path": str(backup_file)}


def cleanup_old_backups(max_keep: int = 20) -> dict[str, Any]:
    paths = ensure_runtime_dirs()
    backups = sorted(path for path in paths["backups"].glob("memories-*.jsonl") if path.is_file())
    removed: list[str] = []
    if len(backups) > max_keep:
        for path in backups[: len(backups) - max_keep]:
            path.unlink(missing_ok=True)
            removed.append(str(path))
    return {"ok": True, "removed_count": len(removed), "removed": removed}


def has_recent_activity(window_seconds: int = 300) -> bool:
    activity = ensure_runtime_dirs()["activity"]
    if not activity.exists():
        return False
    try:
        data = json.loads(activity.read_text(encoding="utf-8"))
        at = data.get("at")
        if not at:
            return False
        dt = datetime.fromisoformat(str(at))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() <= window_seconds
    except (json.JSONDecodeError, OSError, ValueError):
        return False
