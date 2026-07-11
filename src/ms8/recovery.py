"""Verified MS8 runtime backup, restore planning, and safe application."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

BACKUP_MANIFEST_NAME = "manifest.json"
BACKUP_SCHEMA_VERSION = 1
_TRANSIENT_SUFFIXES = (".lock", ".tmp", ".temp", ".swp")
_TRANSIENT_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}


def _get_ms8_home() -> Path:
    from .paths import get_ms8_home

    return get_ms8_home()


def _current_version() -> str:
    from . import __version__

    return __version__


def _current_formats() -> dict[str, Any]:
    from .format_registry import CURRENT_FORMATS

    return asdict(CURRENT_FORMATS)


def _load_format_manifest(root: Path) -> dict[str, Any]:
    from .format_registry import load_format_manifest

    return load_format_manifest(root)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_path(name: str) -> Path:
    raw = str(name)
    if not raw or "\x00" in raw or "\\" in raw:
        raise ValueError(f"unsafe relative path: {raw!r}")
    components = raw.split("/")
    if any(part in {"", ".", ".."} for part in components):
        raise ValueError(f"unsafe relative path: {raw!r}")
    pure = PurePosixPath(raw)
    if pure.is_absolute() or not pure.parts:
        raise ValueError(f"unsafe relative path: {raw!r}")
    first = pure.parts[0]
    if ":" in first:
        raise ValueError(f"unsafe relative path: {raw!r}")
    return Path(*pure.parts)


def _safe_archive_member(name: str) -> bool:
    if name == BACKUP_MANIFEST_NAME:
        return True
    if not name.startswith("runtime/"):
        return False
    try:
        _relative_path(name.removeprefix("runtime/"))
    except ValueError:
        return False
    return True


def _safe_destination(target: Path, relative: Path) -> Path:
    target = target.expanduser().resolve()
    current = target
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"restore path crosses symlink: {relative.as_posix()}")
    destination = target / relative
    if destination.is_symlink():
        raise ValueError(f"restore destination is a symlink: {relative.as_posix()}")
    return destination


def _is_sqlite(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def _sqlite_snapshot(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_uri = f"{source.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(source_uri, uri=True) as source_db:
        with sqlite3.connect(destination) as destination_db:
            source_db.backup(destination_db)


def _should_skip(relative: Path) -> bool:
    if not relative.parts:
        return True
    if relative.parts[0] == "backups":
        return True
    if any(part in _TRANSIENT_DIRS for part in relative.parts):
        return True
    return relative.name == ".DS_Store" or relative.name.endswith(_TRANSIENT_SUFFIXES)


def _stage_runtime(root: Path, stage: Path) -> tuple[list[dict[str, Any]], list[str]]:
    files: list[dict[str, Any]] = []
    skipped_symlinks: list[str] = []
    if not root.exists():
        return files, skipped_symlinks

    for source in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = source.relative_to(root)
        if _should_skip(relative):
            continue
        if source.is_symlink():
            skipped_symlinks.append(relative.as_posix())
            continue
        if not source.is_file():
            continue
        destination = stage / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if _is_sqlite(source):
            _sqlite_snapshot(source, destination)
            snapshot_kind = "sqlite-backup-api"
        else:
            shutil.copy2(source, destination)
            snapshot_kind = "copy"
        files.append(
            {
                "path": relative.as_posix(),
                "size": destination.stat().st_size,
                "sha256": _sha256(destination),
                "snapshot_kind": snapshot_kind,
            }
        )
    return files, skipped_symlinks


def create_runtime_backup(
    *,
    root: Path | None = None,
    output: Path | None = None,
    tag: str = "manual",
) -> dict[str, Any]:
    root = Path(root or _get_ms8_home()).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    backup_dir = root / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    safe_tag = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in tag).strip("-") or "manual"
    archive = Path(output).expanduser().resolve() if output else backup_dir / f"ms8-runtime-{safe_tag}-{_timestamp()}.zip"
    archive.parent.mkdir(parents=True, exist_ok=True)

    temporary_archive = archive.with_name(f".{archive.name}.{os.getpid()}.tmp")
    with tempfile.TemporaryDirectory(prefix="ms8-backup-") as temp_dir:
        stage = Path(temp_dir) / "runtime"
        stage.mkdir(parents=True, exist_ok=True)
        files, skipped_symlinks = _stage_runtime(root, stage)
        try:
            format_manifest = _load_format_manifest(root)
        except ValueError as exc:
            format_manifest = {"runtime_format_version": -1, "error": str(exc)}
        manifest: dict[str, Any] = {
            "backup_schema_version": BACKUP_SCHEMA_VERSION,
            "created_at": _utc_now(),
            "ms8_version": _current_version(),
            "source_root": str(root),
            "format_versions": format_manifest,
            "current_supported_formats": _current_formats(),
            "consistency_scope": "per-file; SQLite uses backup API",
            "files": files,
            "skipped_symlinks": skipped_symlinks,
        }
        with zipfile.ZipFile(temporary_archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
            bundle.writestr(BACKUP_MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
            for row in files:
                relative = _relative_path(str(row["path"]))
                bundle.write(stage / relative, arcname=f"runtime/{relative.as_posix()}")
        os.replace(temporary_archive, archive)

    verification = verify_runtime_backup(archive)
    if not verification.get("ok", False):
        archive.unlink(missing_ok=True)
        return {"ok": False, "path": str(archive), "verification": verification}
    manifest_payload = verification.get("manifest", {})
    return {
        "ok": True,
        "path": str(archive),
        "file_count": len(manifest_payload.get("files", [])) if isinstance(manifest_payload, dict) else 0,
        "sha256": _sha256(archive),
        "skipped_symlinks": manifest_payload.get("skipped_symlinks", []) if isinstance(manifest_payload, dict) else [],
    }


def verify_runtime_backup(archive: Path) -> dict[str, Any]:
    archive = Path(archive).expanduser().resolve()
    errors: list[str] = []
    if not archive.is_file():
        return {"ok": False, "archive": str(archive), "errors": ["archive_not_found"]}
    try:
        with zipfile.ZipFile(archive, "r") as bundle:
            names = bundle.namelist()
            if len(names) != len(set(names)):
                errors.append("duplicate_archive_member")
            errors.extend(f"unsafe_member:{name}" for name in names if not _safe_archive_member(name))
            if BACKUP_MANIFEST_NAME not in names:
                errors.append("manifest_missing")
                return {"ok": False, "archive": str(archive), "errors": errors}
            manifest = json.loads(bundle.read(BACKUP_MANIFEST_NAME).decode("utf-8"))
            if not isinstance(manifest, dict):
                errors.append("manifest_not_object")
                return {"ok": False, "archive": str(archive), "errors": errors}
            if manifest.get("backup_schema_version") != BACKUP_SCHEMA_VERSION:
                errors.append("unsupported_backup_schema")
            declared = manifest.get("files", [])
            if not isinstance(declared, list):
                errors.append("files_not_list")
                declared = []
            declared_names: set[str] = set()
            for row in declared:
                if not isinstance(row, dict):
                    errors.append("invalid_file_entry")
                    continue
                relative_name = str(row.get("path", ""))
                try:
                    relative = _relative_path(relative_name)
                except ValueError:
                    errors.append(f"missing_or_unsafe:{relative_name}")
                    continue
                member = f"runtime/{relative.as_posix()}"
                if member in declared_names:
                    errors.append(f"duplicate_manifest_path:{relative_name}")
                declared_names.add(member)
                if member not in names:
                    errors.append(f"missing_or_unsafe:{relative_name}")
                    continue
                payload = bundle.read(member)
                actual = hashlib.sha256(payload).hexdigest()
                if actual != str(row.get("sha256", "")):
                    errors.append(f"checksum_mismatch:{relative_name}")
                try:
                    expected_size = int(row.get("size", -1))
                except (TypeError, ValueError):
                    expected_size = -1
                if len(payload) != expected_size:
                    errors.append(f"size_mismatch:{relative_name}")
            undeclared = [name for name in names if name.startswith("runtime/") and name not in declared_names]
            errors.extend(f"undeclared_member:{name}" for name in undeclared)
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return {"ok": False, "archive": str(archive), "errors": [f"invalid_archive:{exc}"]}
    return {
        "ok": not errors,
        "archive": str(archive),
        "archive_sha256": _sha256(archive),
        "errors": errors,
        "manifest": manifest,
    }


def plan_runtime_restore(archive: Path, *, target_root: Path | None = None) -> dict[str, Any]:
    target = Path(target_root or _get_ms8_home()).expanduser().resolve()
    verification = verify_runtime_backup(archive)
    if not verification.get("ok", False):
        return {"ok": False, "target_root": str(target), "verification": verification}
    manifest = verification.get("manifest", {})
    files = manifest.get("files", []) if isinstance(manifest, dict) else []
    create: list[str] = []
    overwrite: list[str] = []
    unchanged: list[str] = []
    for row in files:
        relative = _relative_path(str(row["path"]))
        destination = _safe_destination(target, relative)
        if not destination.exists():
            create.append(relative.as_posix())
        elif destination.is_file() and _sha256(destination) == str(row["sha256"]):
            unchanged.append(relative.as_posix())
        else:
            overwrite.append(relative.as_posix())
    return {
        "ok": True,
        "archive": str(Path(archive).expanduser().resolve()),
        "target_root": str(target),
        "create": create,
        "overwrite": overwrite,
        "unchanged": unchanged,
        "delete": [],
        "destructive_delete_enabled": False,
        "requires_confirmation": True,
    }


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.restore-tmp")
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _append_restore_audit(target: Path, event: dict[str, Any]) -> None:
    audit_path = target / "memory" / "logs" / "restore_audit.jsonl"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def restore_runtime_backup(
    archive: Path,
    *,
    target_root: Path | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    target = Path(target_root or _get_ms8_home()).expanduser().resolve()
    try:
        plan = plan_runtime_restore(archive, target_root=target)
    except ValueError as exc:
        return {"ok": False, "target_root": str(target), "applied": False, "error": str(exc)}
    if not plan.get("ok", False) or not apply:
        return {**plan, "applied": False, "dry_run": True}

    target.mkdir(parents=True, exist_ok=True)
    existing_files = [path for path in target.rglob("*") if path.is_file() and "backups" not in path.relative_to(target).parts]
    pre_restore_backup = ""
    if existing_files:
        result = create_runtime_backup(root=target, tag="pre-restore")
        if not result.get("ok", False):
            return {**plan, "ok": False, "applied": False, "error": "pre_restore_backup_failed"}
        pre_restore_backup = str(result.get("path", ""))

    verification = verify_runtime_backup(archive)
    manifest = verification.get("manifest", {})
    rows = manifest.get("files", []) if isinstance(manifest, dict) else []
    try:
        with tempfile.TemporaryDirectory(prefix="ms8-restore-") as temp_dir:
            stage = Path(temp_dir)
            with zipfile.ZipFile(Path(archive).expanduser().resolve(), "r") as bundle:
                for row in rows:
                    relative = _relative_path(str(row["path"]))
                    member = f"runtime/{relative.as_posix()}"
                    staged_file = stage / relative
                    staged_file.parent.mkdir(parents=True, exist_ok=True)
                    staged_file.write_bytes(bundle.read(member))
                    if _sha256(staged_file) != str(row["sha256"]):
                        return {
                            **plan,
                            "ok": False,
                            "applied": False,
                            "error": f"staged_checksum_mismatch:{relative}",
                            "pre_restore_backup": pre_restore_backup,
                        }
            for row in rows:
                relative = _relative_path(str(row["path"]))
                destination = _safe_destination(target, relative)
                _atomic_copy(stage / relative, destination)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        failure_event = {
            "event": "runtime_restore_failed",
            "at": _utc_now(),
            "archive": str(Path(archive).expanduser().resolve()),
            "archive_sha256": verification.get("archive_sha256", ""),
            "pre_restore_backup": pre_restore_backup,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        _append_restore_audit(target, failure_event)
        return {
            **plan,
            "ok": False,
            "applied": False,
            "dry_run": False,
            "error": f"restore_apply_failed:{type(exc).__name__}",
            "pre_restore_backup": pre_restore_backup,
        }

    event = {
        "event": "runtime_restore",
        "at": _utc_now(),
        "archive": str(Path(archive).expanduser().resolve()),
        "archive_sha256": verification.get("archive_sha256", ""),
        "pre_restore_backup": pre_restore_backup,
        "created": len(plan["create"]),
        "overwritten": len(plan["overwrite"]),
        "unchanged": len(plan["unchanged"]),
    }
    _append_restore_audit(target, event)
    return {**plan, "ok": True, "applied": True, "dry_run": False, "pre_restore_backup": pre_restore_backup}
