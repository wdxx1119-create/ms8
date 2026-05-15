"""Runtime lifecycle operations: clean/reset/uninstall."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .runtime import ensure_runtime_dirs, get_runtime_dir
from .service import remove_service


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _is_subpath(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def _remove_path(path: Path, *, dry_run: bool) -> tuple[bool, str]:
    if not path.exists():
        return False, ""
    if dry_run:
        return True, ""
    try:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=False)
        else:
            path.unlink(missing_ok=True)
        return True, ""
    except OSError as exc:
        return False, str(exc)


def _copy_if_exists(src: Path, dst: Path, *, dry_run: bool) -> bool:
    if not src.exists():
        return False
    if dry_run:
        return True
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dst)
    return True


def clean_runtime(*, dry_run: bool = True) -> dict[str, Any]:
    paths = ensure_runtime_dirs()
    root = paths["root"]
    targets = [
        root / "logs",
        root / "health",
        root / ".mcp_connect_tmp",
        root / ".ms8_write_probe",
        root / "memory" / "__pycache__",
        root / "connect" / "runtime",
    ]
    removed: list[str] = []
    failed: list[dict[str, str]] = []
    for target in targets:
        ok, err = _remove_path(target, dry_run=dry_run)
        if ok:
            removed.append(str(target))
        elif err:
            failed.append({"path": str(target), "error": err})
    if not dry_run:
        # recreate mandatory runtime dirs
        ensure_runtime_dirs()
    return {
        "ok": len(failed) == 0,
        "operation": "clean",
        "dry_run": dry_run,
        "removed_count": len(removed),
        "removed": removed,
        "failed_count": len(failed),
        "failed": failed,
    }


def reset_runtime(*, dry_run: bool = True, backup: bool = True) -> dict[str, Any]:
    paths = ensure_runtime_dirs()
    root = paths["root"]
    stamp = _utc_stamp()
    backup_root = root / "backups" / f"reset-{stamp}"
    preserved_sources = [
        root / "memory" / "auto_memory_records.jsonl",
        root / "memory" / "knowledge_graph.db",
        root / "MEMORY.md",
        root / "memory" / "memory_blocks.json",
    ]
    copied: list[str] = []
    if backup:
        for src in preserved_sources:
            rel = src.relative_to(root) if src.exists() else None
            if rel is None:
                continue
            dst = backup_root / rel
            if _copy_if_exists(src, dst, dry_run=dry_run):
                copied.append(str(src))

    # Reset derived/ephemeral state; keep base memory data files intact.
    targets = [
        root / "health",
        root / "logs",
        root / "memory" / "reports",
        root / "memory" / "auto_memory_index.json",
        root / "memory" / "auto_memory_review_queue.jsonl",
        root / "memory" / "compression_state.json",
        root / "memory" / "maintenance_state.json",
        root / "memory" / "noncanonical_quarantine.jsonl",
        root / "memory" / "governance_runtime_snapshot.json",
    ]
    removed: list[str] = []
    failed: list[dict[str, str]] = []
    for target in targets:
        ok, err = _remove_path(target, dry_run=dry_run)
        if ok:
            removed.append(str(target))
        elif err:
            failed.append({"path": str(target), "error": err})
    if not dry_run:
        ensure_runtime_dirs()
    return {
        "ok": len(failed) == 0,
        "operation": "reset",
        "dry_run": dry_run,
        "backup_enabled": backup,
        "backup_path": str(backup_root) if backup else "",
        "backup_items": copied,
        "removed_count": len(removed),
        "removed": removed,
        "failed_count": len(failed),
        "failed": failed,
    }


def uninstall_runtime(
    *,
    dry_run: bool = True,
    purge_data: bool = False,
    backup: bool = True,
    remove_launchd: bool = True,
) -> dict[str, Any]:
    root = get_runtime_dir()
    stamp = _utc_stamp()
    backup_root = Path.home() / ".ms8_uninstall_backups" / root.name / f"uninstall-{stamp}"
    backup_guard_error = ""
    if _is_subpath(backup_root, root):
        backup_guard_error = "backup_root_inside_runtime_root"
    copied: list[str] = []
    if backup and not backup_guard_error:
        preserve = [
            root / "memory" / "auto_memory_records.jsonl",
            root / "memory" / "knowledge_graph.db",
            root / "MEMORY.md",
            root / "memory" / "memory_blocks.json",
            root / "memory" / "security",
        ]
        for src in preserve:
            if not src.exists():
                continue
            rel = src.relative_to(root)
            dst = backup_root / rel
            if _copy_if_exists(src, dst, dry_run=dry_run):
                copied.append(str(src))

    service_out: dict[str, Any] = {"ok": True, "skipped": True}
    if remove_launchd:
        if dry_run:
            service_out = {"ok": True, "skipped": True, "reason": "dry_run"}
        else:
            service_out = remove_service()

    removed_root = False
    removed: list[str] = []
    failed: list[dict[str, str]] = []
    if backup_guard_error:
        return {
            "ok": False,
            "operation": "uninstall",
            "dry_run": dry_run,
            "purge_data": purge_data,
            "backup_enabled": backup,
            "backup_path": str(backup_root) if backup else "",
            "backup_items": copied,
            "backup_verified": False,
            "backup_error": backup_guard_error,
            "service": service_out,
            "runtime_root": str(root),
            "runtime_root_removed": False,
            "removed_count": 0,
            "removed": [],
            "failed_count": 0,
            "failed": [],
        }
    if purge_data:
        ok, err = _remove_path(root, dry_run=dry_run)
        removed_root = ok
        if ok:
            removed.append(str(root))
        elif err:
            failed.append({"path": str(root), "error": err})
    else:
        targets = [
            root / "logs",
            root / "health",
            root / "data",
            root / "connect",
        ]
        for target in targets:
            ok, err = _remove_path(target, dry_run=dry_run)
            if ok:
                removed.append(str(target))
            elif err:
                failed.append({"path": str(target), "error": err})
        removed_root = False
    return {
        "ok": len(failed) == 0,
        "operation": "uninstall",
        "dry_run": dry_run,
        "purge_data": purge_data,
        "backup_enabled": backup,
        "backup_path": str(backup_root) if backup else "",
        "backup_items": copied,
        "backup_verified": backup and not backup_guard_error,
        "service": service_out,
        "runtime_root": str(root),
        "runtime_root_removed": removed_root,
        "removed_count": len(removed),
        "removed": removed,
        "failed_count": len(failed),
        "failed": failed,
    }


def render_lifecycle_result(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False, indent=2)
