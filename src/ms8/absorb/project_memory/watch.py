"""Foreground watcher for absorb project-memory."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

from .generator import build_outputs
from .scanner import ALLOWED_SUFFIXES, EXCLUDED_DIRS, SPECIAL_NAMES, scan_project
from .search import rebuild_search_index
from .scope import load_watch_state, save_watch_state
from .submit import submit_project_summary

logger = logging.getLogger(__name__)


def _update_watch_state(
    path: Path,
    *,
    running: bool,
    started_at: float | None = None,
    cycles_run: int | None = None,
    last_payload: dict[str, Any] | None = None,
    last_error: str = "",
) -> dict[str, Any]:
    state = load_watch_state(path)
    now_iso = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    state["running"] = bool(running)
    state["pid"] = int(os.getpid()) if running else 0
    state["heartbeat_at"] = now_iso
    if started_at is not None and not state.get("started_at"):
        state["started_at"] = __import__("datetime").datetime.fromtimestamp(started_at, __import__("datetime").timezone.utc).isoformat()
    if not running:
        state["stopped_at"] = now_iso
    if cycles_run is not None:
        state["cycles_run"] = int(cycles_run)
    if last_payload is not None:
        state["last_cycle"] = dict(last_payload)
        state["last_status"] = str(last_payload.get("status", "") or "")
    if last_error:
        state["last_error"] = last_error
    elif last_payload is not None and bool(last_payload.get("ok", False)):
        state["last_error"] = ""
    save_watch_state(path, state)
    return state


def _should_track(path: Path) -> bool:
    if any(part in EXCLUDED_DIRS for part in path.parts):
        return False
    if path.name.startswith("."):
        return False
    if path.suffix.lower() in ALLOWED_SUFFIXES:
        return True
    upper = path.name.upper()
    return any(upper.startswith(prefix) for prefix in SPECIAL_NAMES)


def run_project_cycle(
    *,
    project_name: str,
    project_root: Path,
    db_path: Path,
    whoosh_dir: Path,
    output_dir: Path,
    index_state_path: Path,
    build_state_path: Path,
    auto_index: bool = True,
    auto_build: bool = False,
    auto_submit_main_memory: bool = False,
    previous_summary_hash: str = "",
) -> dict[str, Any]:
    scan_payload = scan_project(
        project_name=project_name,
        project_root=project_root,
        db_path=db_path,
        index_state_path=index_state_path,
    )
    result: dict[str, Any] = {
        "ok": bool(scan_payload.get("ok", False)),
        "status": "scanned",
        "scan": scan_payload,
    }
    if auto_index:
        index_payload = rebuild_search_index(
            db_path,
            whoosh_dir,
            index_state_path,
            full_rebuild=False,
        )
        result["index"] = index_payload
        result["ok"] = bool(result["ok"]) and bool(index_payload.get("ok", False))
        result["status"] = "indexed" if bool(index_payload.get("ok", False)) else "index_failed"
    if auto_build and result.get("ok", False):
        changed_paths = list(scan_payload.get("changed_paths", []) or [])
        changed_paths.extend(list(scan_payload.get("deleted_paths", []) or []))
        build_payload = build_outputs(
            project_name=project_name,
            project_root=project_root,
            db_path=db_path,
            output_dir=output_dir,
            build_state_path=build_state_path,
            changed_paths=changed_paths,
        )
        result["build"] = build_payload
        result["ok"] = bool(result["ok"]) and bool(build_payload.get("ok", False))
        if bool(build_payload.get("ok", False)):
            result["status"] = "built" if str(build_payload.get("status", "")) != "up_to_date" else "up_to_date"
        else:
            result["status"] = "build_failed"
    if auto_submit_main_memory and result.get("ok", False) and auto_build:
        submit_payload = submit_project_summary(
            project_name=project_name,
            project_root=project_root,
            output_dir=output_dir,
            previous_hash=previous_summary_hash,
        )
        result["main_memory_submit"] = submit_payload
        result["ok"] = bool(result["ok"]) and bool(submit_payload.get("ok", False))
        if submit_payload.get("status") == "submitted":
            result["status"] = "submitted"
    return result


def _safe_run_project_cycle(**kwargs: Any) -> dict[str, Any]:
    try:
        return run_project_cycle(**kwargs)
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        logger.warning("project-memory cycle failed: %s", exc)
        return {
            "ok": False,
            "status": "cycle_failed",
            "error": str(exc),
        }


def watch_project(
    *,
    project_name: str,
    project_root: Path,
    db_path: Path,
    whoosh_dir: Path,
    output_dir: Path,
    index_state_path: Path,
    watch_state_path: Path,
    build_state_path: Path,
    duration: float | None = None,
    debounce_seconds: float = 1.0,
    auto_index: bool = True,
    auto_build: bool = False,
    auto_submit_main_memory: bool = False,
    previous_summary_hash: str = "",
    bootstrap: bool = True,
) -> dict[str, Any]:
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError as exc:
        return {
            "ok": False,
            "status": "missing_dependency",
            "reason": f"install ms8[absorb] for watchdog support: {exc}",
        }

    events_seen = 0
    changed_paths: set[str] = set()
    last_event_at = 0.0
    cycles: list[dict[str, Any]] = []

    class ProjectMemoryHandler(FileSystemEventHandler):
        def on_any_event(self, event: Any) -> None:
            nonlocal events_seen, last_event_at
            if getattr(event, "is_directory", False):
                return
            src_path = Path(str(getattr(event, "src_path", ""))).expanduser()
            try:
                rel = src_path.resolve().relative_to(project_root.resolve()).as_posix()
            except (OSError, RuntimeError, ValueError):
                return
            if not _should_track(Path(rel)):
                return
            changed_paths.add(rel)
            events_seen += 1
            last_event_at = time.time()

    observer = Observer()
    handler = ProjectMemoryHandler()
    observer.schedule(handler, str(project_root), recursive=True)
    observer.start()
    started_at = time.time()
    last_cycle_payload: dict[str, Any] | None = None
    last_summary_hash = str(previous_summary_hash or "")
    _update_watch_state(watch_state_path, running=True, started_at=started_at, cycles_run=0)
    try:
        if bootstrap:
            last_cycle_payload = _safe_run_project_cycle(
                project_name=project_name,
                project_root=project_root,
                db_path=db_path,
                whoosh_dir=whoosh_dir,
                output_dir=output_dir,
                index_state_path=index_state_path,
                build_state_path=build_state_path,
                auto_index=auto_index,
                auto_build=auto_build,
                auto_submit_main_memory=auto_submit_main_memory,
                previous_summary_hash=last_summary_hash,
            )
            cycles.append(last_cycle_payload)
            _update_watch_state(
                watch_state_path,
                running=True,
                cycles_run=len(cycles),
                last_payload=last_cycle_payload,
                last_error=str(last_cycle_payload.get("error", "") or ""),
            )
            submit_payload = dict(last_cycle_payload.get("main_memory_submit", {}) or {})
            if str(submit_payload.get("status", "")) == "submitted":
                last_summary_hash = str(submit_payload.get("content_hash", "") or last_summary_hash)
        deadline = None if duration is None else started_at + max(0.0, float(duration))
        while True:
            time.sleep(0.2)
            now = time.time()
            if changed_paths and last_event_at and now - last_event_at >= max(0.1, float(debounce_seconds)):
                cycle_payload = _safe_run_project_cycle(
                    project_name=project_name,
                    project_root=project_root,
                    db_path=db_path,
                    whoosh_dir=whoosh_dir,
                    output_dir=output_dir,
                    index_state_path=index_state_path,
                    build_state_path=build_state_path,
                    auto_index=auto_index,
                    auto_build=auto_build,
                    auto_submit_main_memory=auto_submit_main_memory,
                    previous_summary_hash=last_summary_hash,
                )
                cycle_payload["trigger_paths"] = sorted(changed_paths)
                cycles.append(cycle_payload)
                last_cycle_payload = cycle_payload
                _update_watch_state(
                    watch_state_path,
                    running=True,
                    cycles_run=len(cycles),
                    last_payload=cycle_payload,
                    last_error=str(cycle_payload.get("error", "") or ""),
                )
                submit_payload = dict(cycle_payload.get("main_memory_submit", {}) or {})
                if str(submit_payload.get("status", "")) == "submitted":
                    last_summary_hash = str(submit_payload.get("content_hash", "") or last_summary_hash)
                changed_paths.clear()
            if deadline is not None and now >= deadline:
                break
    except KeyboardInterrupt:
        logger.info("project-memory watcher interrupted by user")
    finally:
        observer.stop()
        observer.join(timeout=5)
        _update_watch_state(
            watch_state_path,
            running=False,
            cycles_run=len(cycles),
            last_payload=last_cycle_payload or {},
            last_error=str((last_cycle_payload or {}).get("error", "") or ""),
        )

    return {
        "ok": all(bool(item.get("ok", False)) for item in cycles) if cycles else True,
        "status": "stopped",
        "project": project_name,
        "root": str(project_root),
        "duration": round(time.time() - started_at, 2),
        "events_seen": events_seen,
        "cycles_run": len(cycles),
        "auto_index": auto_index,
        "auto_build": auto_build,
        "watch_state_path": str(watch_state_path),
        "last_cycle": last_cycle_payload or {},
    }
