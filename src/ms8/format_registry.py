"""Versioned runtime-format manifest and migration registry.

The registry is intentionally small and explicit. Runtime migrations must preserve
unknown fields, create a backup before mutation, and append an audit record.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FORMAT_MANIFEST_NAME = "format_manifest.json"
CURRENT_RUNTIME_FORMAT_VERSION = 1


@dataclass(frozen=True)
class FormatVersions:
    runtime_format_version: int = CURRENT_RUNTIME_FORMAT_VERSION
    canonical_record_schema_version: int = 1
    absorb_schema_version: int = 1
    graph_schema_version: int = 1
    index_format_version: int = 1
    config_schema_version: int = 1


CURRENT_FORMATS = FormatVersions()
Migration = Callable[[dict[str, Any]], dict[str, Any]]
_MIGRATIONS: dict[tuple[int, int], Migration] = {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _append_audit(root: Path, payload: dict[str, Any]) -> Path:
    path = root / "memory" / "logs" / "migration_audit.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return path


def register_migration(source: int, target: int) -> Callable[[Migration], Migration]:
    if target != source + 1:
        raise ValueError("runtime migrations must advance exactly one version")

    def decorator(function: Migration) -> Migration:
        key = (source, target)
        if key in _MIGRATIONS:
            raise ValueError(f"duplicate runtime migration: {source}->{target}")
        _MIGRATIONS[key] = function
        return function

    return decorator


def manifest_path(root: Path) -> Path:
    return Path(root).expanduser().resolve() / FORMAT_MANIFEST_NAME


def load_format_manifest(root: Path) -> dict[str, Any]:
    path = manifest_path(root)
    if not path.exists():
        return {
            "manifest_schema_version": 1,
            "runtime_format_version": 0,
            "legacy_runtime": True,
            "detected_at": _utc_now(),
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid runtime format manifest: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"runtime format manifest must be an object: {path}")
    version = payload.get("runtime_format_version")
    if not isinstance(version, int) or version < 0:
        raise ValueError(f"invalid runtime_format_version in {path}")
    return payload


def current_format_manifest(*, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(previous or {})
    payload.update(asdict(CURRENT_FORMATS))
    payload["manifest_schema_version"] = 1
    payload["legacy_runtime"] = False
    payload.setdefault("created_at", _utc_now())
    payload["updated_at"] = _utc_now()
    return payload


def ensure_format_manifest(root: Path) -> dict[str, Any]:
    root = Path(root).expanduser().resolve()
    existing = load_format_manifest(root)
    if int(existing.get("runtime_format_version", 0)) > CURRENT_RUNTIME_FORMAT_VERSION:
        raise ValueError(
            "runtime format is newer than this MS8 build: "
            f"{existing['runtime_format_version']} > {CURRENT_RUNTIME_FORMAT_VERSION}"
        )
    if int(existing.get("runtime_format_version", 0)) == CURRENT_RUNTIME_FORMAT_VERSION:
        return existing
    return apply_runtime_migrations(root, target_version=CURRENT_RUNTIME_FORMAT_VERSION)


def plan_runtime_migrations(root: Path, *, target_version: int = CURRENT_RUNTIME_FORMAT_VERSION) -> dict[str, Any]:
    root = Path(root).expanduser().resolve()
    manifest = load_format_manifest(root)
    current = int(manifest.get("runtime_format_version", 0))
    if target_version < current:
        raise ValueError("runtime format downgrade is not supported")
    steps: list[dict[str, int]] = []
    cursor = current
    while cursor < target_version:
        key = (cursor, cursor + 1)
        if key not in _MIGRATIONS:
            raise ValueError(f"missing runtime migration: {cursor}->{cursor + 1}")
        steps.append({"from": cursor, "to": cursor + 1})
        cursor += 1
    return {
        "ok": True,
        "root": str(root),
        "current_version": current,
        "target_version": target_version,
        "steps": steps,
        "requires_backup": bool(steps),
    }


def apply_runtime_migrations(root: Path, *, target_version: int = CURRENT_RUNTIME_FORMAT_VERSION) -> dict[str, Any]:
    root = Path(root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    plan = plan_runtime_migrations(root, target_version=target_version)
    if not plan["steps"]:
        return load_format_manifest(root)

    # Import lazily to avoid a module cycle. Every mutating migration receives a
    # verified pre-migration snapshot.
    from .recovery import create_runtime_backup

    backup = create_runtime_backup(root=root, tag="pre-migration")
    if not backup.get("ok", False):
        raise RuntimeError("pre-migration backup failed")

    payload = load_format_manifest(root)
    applied: list[dict[str, int]] = []
    for step in plan["steps"]:
        source = int(step["from"])
        target = int(step["to"])
        payload = _MIGRATIONS[(source, target)](dict(payload))
        payload["runtime_format_version"] = target
        payload["updated_at"] = _utc_now()
        applied.append({"from": source, "to": target})

    _atomic_write_json(manifest_path(root), payload)
    audit = {
        "event": "runtime_format_migration",
        "at": _utc_now(),
        "from_version": int(plan["current_version"]),
        "to_version": int(plan["target_version"]),
        "steps": applied,
        "backup": str(backup.get("path", "")),
    }
    _append_audit(root, audit)
    return payload


@register_migration(0, 1)
def _migrate_legacy_runtime_to_v1(payload: dict[str, Any]) -> dict[str, Any]:
    """Adopt an existing unversioned runtime without rewriting its data."""

    return current_format_manifest(previous=payload)
