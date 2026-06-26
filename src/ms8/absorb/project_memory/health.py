"""Health and status helpers for absorb project-memory."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .repository import active_chunks, stats
from .scope import load_build_state, load_index_state, load_registry, load_watch_state, project_dir_paths


def _watch_support() -> dict[str, Any]:
    try:
        import watchdog  # noqa: F401

        return {"installed": True, "backend": "watchdog"}
    except ImportError:
        return {"installed": False, "backend": "watchdog"}


def _sqlite_health(db_path: Path) -> dict[str, Any]:
    if not db_path.exists():
        return {
            "exists": False,
            "readable": False,
            "writable": False,
            "query_ok": False,
            "journal_mode": "",
            "busy_timeout_ms": 0,
        }
    try:
        conn = sqlite3.connect(db_path)
        try:
            journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0] or "")
            busy_timeout = int(conn.execute("PRAGMA busy_timeout").fetchone()[0] or 0)
            conn.execute("SELECT 1").fetchone()
        finally:
            conn.close()
        return {
            "exists": True,
            "readable": True,
            "writable": True,
            "query_ok": True,
            "journal_mode": journal_mode.lower(),
            "busy_timeout_ms": busy_timeout,
        }
    except (OSError, RuntimeError, ValueError, sqlite3.DatabaseError) as exc:
        return {
            "exists": True,
            "readable": False,
            "writable": False,
            "query_ok": False,
            "journal_mode": "",
            "busy_timeout_ms": 0,
            "error": str(exc),
        }


def _whoosh_health(whoosh_dir: Path) -> dict[str, Any]:
    files = [item for item in whoosh_dir.glob("*") if item.is_file()] if whoosh_dir.exists() else []
    return {
        "exists": whoosh_dir.exists(),
        "file_count": len(files),
    }


def _runtime_mode(service_state: dict[str, Any], watch_support: dict[str, Any]) -> dict[str, Any]:
    service_installed = bool(service_state.get("installed", False))
    service_running = bool(service_state.get("running", False))
    watch_installed = bool(watch_support.get("installed", False))
    error_kind = str(service_state.get("error_kind", "") or "")

    background_ready = service_installed and service_running
    foreground_ready = watch_installed
    if background_ready:
        return {
            "background_service_ready": True,
            "foreground_watch_available": foreground_ready,
            "recommended_runtime_mode": "background_service",
            "runtime_hint": "Background scheduler is installed and running.",
        }
    if foreground_ready:
        hint = "Foreground watch is available."
        if error_kind == "permission_denied":
            hint = "Background scheduler is blocked by Windows permissions; use foreground watch."
        return {
            "background_service_ready": False,
            "foreground_watch_available": True,
            "recommended_runtime_mode": "foreground_watch",
            "runtime_hint": hint,
        }
    return {
        "background_service_ready": False,
        "foreground_watch_available": False,
        "recommended_runtime_mode": "manual_scan_index_build",
        "runtime_hint": "Install absorb/watch dependencies or use manual scan/index/build commands.",
    }


def project_status(
    *,
    name: str,
    root: str,
    db_path: Path,
    whoosh_dir: Path,
    output_dir: Path,
    index_state_path: Path,
    build_state_path: Path,
) -> dict[str, Any]:
    snapshot = stats(db_path)
    index_state = load_index_state(index_state_path)
    build_state = load_build_state(build_state_path)
    registry = load_registry()
    project_cfg = dict(registry.get("projects", {}).get(name, {}) or {})
    watch_state = load_watch_state(project_dir_paths(name)["watch_state_path"])
    sqlite_health = _sqlite_health(db_path)
    whoosh_health = _whoosh_health(whoosh_dir)
    try:
        from ...service import project_memory_service_status

        service_state = project_memory_service_status(name)
    except (ImportError, OSError, RuntimeError, ValueError):
        service_state = {"ok": False, "installed": False, "running": False}
    whoosh_ready = bool(index_state.get("search_index_ready", False)) and whoosh_dir.exists()
    watch_support = _watch_support()
    runtime_mode = _runtime_mode(service_state, watch_support)
    return {
        "ok": True,
        "name": name,
        "root": root,
        "db_path": str(db_path),
        "db_readable": bool(sqlite_health.get("readable", False)),
        "db_writable": bool(sqlite_health.get("writable", False)),
        "db_query_ok": bool(sqlite_health.get("query_ok", False)),
        "content_db_ready": bool(sqlite_health.get("query_ok", False)),
        "sqlite_health": sqlite_health,
        "whoosh_exists": bool(whoosh_health.get("exists", False)),
        "whoosh_file_count": int(whoosh_health.get("file_count", 0) or 0),
        "whoosh_health": whoosh_health,
        "search_index_ready": whoosh_ready,
        "index_status": str(index_state.get("status", "missing")),
        "index_state_path": str(index_state_path),
        "changed_files_pending": int(index_state.get("changed_files_pending", 0) or 0),
        "last_index_at": str(index_state.get("last_index_at", "")),
        "last_full_rebuild_at": str(index_state.get("last_full_rebuild_at", "")),
        "index_last_error": str(index_state.get("last_error", "")),
        "file_count": snapshot["file_count"],
        "file_types": snapshot["file_types"],
        "chunk_count": snapshot["chunk_count"],
        "last_scan_at": snapshot["last_scan_at"],
        "auto_write_main_memory": bool(project_cfg.get("auto_write_main_memory", False)),
        "last_summary_hash": str(project_cfg.get("last_summary_hash", "") or ""),
        "last_summary_record_id": str(project_cfg.get("last_summary_record_id", "") or ""),
        "last_summary_submitted_at": str(project_cfg.get("last_summary_submitted_at", "") or ""),
        "build_state_path": str(build_state_path),
        "last_build_at": str(build_state.get("last_build_at", "")),
        "build_snapshot_hash": str(build_state.get("snapshot_hash", "")),
        "build_last_error": str(build_state.get("last_error", "")),
        "watch_state": watch_state,
        "service_state": service_state,
        "output_exists": output_dir.exists(),
        "watch_support": watch_support,
        **runtime_mode,
        "next_actions": [
            f"ms8 absorb project-memory scan --name {name}",
            f"ms8 absorb project-memory index --name {name}",
            f"ms8 absorb project-memory build --name {name}",
            f"ms8 absorb project-memory submit --name {name}",
            f"ms8 absorb project-memory watch --name {name}",
            f"ms8 absorb project-memory service-install --name {name}",
            f"ms8 absorb project-memory search <query> --name {name}",
        ],
    }


def project_doctor(
    *,
    name: str,
    root: str,
    db_path: Path,
    whoosh_dir: Path,
    output_dir: Path,
    index_state_path: Path,
    build_state_path: Path,
) -> dict[str, Any]:
    status = project_status(
        name=name,
        root=root,
        db_path=db_path,
        whoosh_dir=whoosh_dir,
        output_dir=output_dir,
        index_state_path=index_state_path,
        build_state_path=build_state_path,
    )
    checks: list[dict[str, Any]] = []

    def add_check(check_id: str, ok: bool, message: str, **details: Any) -> None:
        checks.append({"check_id": check_id, "ok": bool(ok), "message": message, "details": details})

    add_check("content_db_exists", db_path.exists(), "SQLite content database available", path=str(db_path))
    sqlite_health = status.get("sqlite_health", {}) if isinstance(status.get("sqlite_health", {}), dict) else {}
    add_check(
        "content_db_query",
        bool(sqlite_health.get("query_ok", False)),
        "SQLite content database accepts basic queries",
        journal_mode=str(sqlite_health.get("journal_mode", "")),
        busy_timeout_ms=int(sqlite_health.get("busy_timeout_ms", 0) or 0),
        error=str(sqlite_health.get("error", "")),
    )
    add_check(
        "index_state_exists",
        index_state_path.exists(),
        "index state file available",
        path=str(index_state_path),
    )
    add_check("output_dir_exists", output_dir.exists(), "output directory available", path=str(output_dir))

    snapshot = stats(db_path)
    active = active_chunks(db_path) if db_path.exists() else []
    add_check(
        "chunk_consistency",
        snapshot["chunk_count"] == len(active),
        "chunk table and active chunk view are aligned",
        chunk_count=snapshot["chunk_count"],
        active_chunks=len(active),
    )

    state = load_index_state(index_state_path)
    if bool(state.get("search_index_ready", False)):
        add_check(
            "whoosh_ready",
            whoosh_dir.exists(),
            "Whoosh index directory available for ready state",
            path=str(whoosh_dir),
            file_count=int(status.get("whoosh_file_count", 0) or 0),
        )
    else:
        add_check(
            "whoosh_ready",
            True,
            "Whoosh index not ready yet; SQLite fallback remains available",
            status=str(state.get("status", "missing")),
            file_count=int(status.get("whoosh_file_count", 0) or 0),
        )

    watch_support = _watch_support()
    add_check(
        "watch_support",
        True,
        "watchdog dependency state recorded for realtime project-memory watch",
        backend=str(watch_support.get("backend", "")),
        installed=bool(watch_support.get("installed", False)),
    )
    watch_state = status.get("watch_state", {})
    running = bool(watch_state.get("running", False)) if isinstance(watch_state, dict) else False
    add_check(
        "watch_state",
        True,
        "watch runtime state recorded for diagnosis",
        running=running,
        heartbeat_at=str(watch_state.get("heartbeat_at", "") if isinstance(watch_state, dict) else ""),
        cycles_run=int(watch_state.get("cycles_run", 0) if isinstance(watch_state, dict) else 0),
    )
    service_state = status.get("service_state", {})
    add_check(
        "service_state",
        True,
        "background service state recorded for project-memory watch",
        installed=bool(service_state.get("installed", False)) if isinstance(service_state, dict) else False,
        running=bool(service_state.get("running", False)) if isinstance(service_state, dict) else False,
        label=str(service_state.get("label", "") if isinstance(service_state, dict) else ""),
        backend=str(service_state.get("backend", "") if isinstance(service_state, dict) else ""),
        scheduler_state=str(service_state.get("scheduler_state", "") if isinstance(service_state, dict) else ""),
        reason_code=str(service_state.get("reason_code", "") if isinstance(service_state, dict) else ""),
        error_kind=str(service_state.get("error_kind", "") if isinstance(service_state, dict) else ""),
    )
    add_check(
        "runtime_mode",
        True,
        "recommended runtime mode derived from background service and foreground watch availability",
        recommended_runtime_mode=str(status.get("recommended_runtime_mode", "")),
        background_service_ready=bool(status.get("background_service_ready", False)),
        foreground_watch_available=bool(status.get("foreground_watch_available", False)),
        runtime_hint=str(status.get("runtime_hint", "")),
    )

    ai_context = output_dir / "AI_CONTEXT.md"
    project_summary = output_dir / "project_summary.md"
    relations = output_dir / "relations.jsonl"
    outputs_ok = ai_context.exists() and project_summary.exists() and relations.exists()
    add_check(
        "build_outputs",
        outputs_ok,
        "project understanding outputs present",
        ai_context=str(ai_context),
        project_summary=str(project_summary),
        relations=str(relations),
    )
    add_check(
        "build_state",
        build_state_path.exists(),
        "build state file available for incremental rebuilds",
        path=str(build_state_path),
        last_build_at=str(status.get("last_build_at", "")),
    )

    overall_ok = all(bool(item.get("ok", False)) for item in checks)
    return {
        "ok": overall_ok,
        "name": name,
        "root": root,
        "status": "healthy" if overall_ok else "degraded",
        "checks": checks,
        "summary": {
            "total": len(checks),
            "pass": sum(1 for item in checks if bool(item.get("ok", False))),
            "fail": sum(1 for item in checks if not bool(item.get("ok", False))),
        },
        "status_snapshot": status,
        "next_actions": status.get("next_actions", []),
    }


def registered_projects_health() -> dict[str, Any]:
    registry = load_registry()
    projects = registry.get("projects", {})
    if not isinstance(projects, dict) or not projects:
        return {
            "ok": True,
            "registered_projects": 0,
            "healthy_projects": 0,
            "stale_projects": 0,
            "degraded_projects": 0,
            "missing_outputs": 0,
            "items": [],
            "risk": "green",
            "reason": "no_registered_projects",
        }

    items: list[dict[str, Any]] = []
    healthy = 0
    stale = 0
    degraded = 0
    missing_outputs = 0
    for project_name, item in sorted(projects.items()):
        if not isinstance(item, dict):
            continue
        root = str(item.get("root", "") or "")
        paths = project_dir_paths(str(project_name))
        status = project_status(
            name=str(project_name),
            root=root,
            db_path=paths["db_path"],
            whoosh_dir=paths["whoosh_dir"],
            output_dir=paths["output_dir"],
            index_state_path=paths["index_state_path"],
            build_state_path=paths["build_state_path"],
        )
        index_status = str(status.get("index_status", "missing") or "missing")
        output_exists = bool(status.get("output_exists", False))
        if index_status == "ready" and output_exists:
            healthy += 1
        elif index_status in {"stale", "missing"}:
            stale += 1
        else:
            degraded += 1
        if not output_exists:
            missing_outputs += 1
        items.append(
            {
                "name": str(project_name),
                "root": root,
                "index_status": index_status,
                "search_index_ready": bool(status.get("search_index_ready", False)),
                "changed_files_pending": int(status.get("changed_files_pending", 0) or 0),
                "output_exists": output_exists,
                "file_count": int(status.get("file_count", 0) or 0),
                "chunk_count": int(status.get("chunk_count", 0) or 0),
            }
        )

    risk = "green"
    if degraded > 0:
        risk = "red"
    elif stale > 0 or missing_outputs > 0:
        risk = "yellow"
    return {
        "ok": True,
        "registered_projects": len(items),
        "healthy_projects": healthy,
        "stale_projects": stale,
        "degraded_projects": degraded,
        "missing_outputs": missing_outputs,
        "items": items,
        "risk": risk,
    }
