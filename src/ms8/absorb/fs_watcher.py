"""Filesystem watcher for authorized absorb roots."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from .incremental_processor import process_delete, process_file, process_pending
from .repository import add_ingest_job, log_event, upsert_file_record
from .scope import DEFAULT_EXCLUDES, is_path_allowed, list_allowed_roots
from .spotlight_bootstrap import bootstrap_authorized_roots

IGNORED_PARTS = set(DEFAULT_EXCLUDES)
logger = logging.getLogger(__name__)


def should_ignore_path(path: str | Path) -> bool:
    p = Path(path).expanduser()
    if any(part in IGNORED_PARTS for part in p.parts):
        return True
    if p.name.startswith("."):
        return True
    return not is_path_allowed(p)


def wait_until_file_stable(path: str | Path, *, checks: int = 3, interval: float = 0.2) -> bool:
    p = Path(path).expanduser()
    last: tuple[int, float] | None = None
    for _ in range(max(1, checks)):
        if not p.exists() or not p.is_file():
            return False
        stat = p.stat()
        current = (stat.st_size, stat.st_mtime)
        if last is not None and current == last:
            return True
        last = current
        time.sleep(interval)
    return p.exists() and p.is_file()


def event_to_file_record(event: Any) -> dict[str, Any]:
    src = Path(getattr(event, "src_path", "")).expanduser().resolve()
    stat = src.stat() if src.exists() else None
    return {
        "event_type": getattr(event, "event_type", "unknown"),
        "path": str(src),
        "canonical_path": str(src),
        "file_type": src.suffix.lower(),
        "size": stat.st_size if stat else 0,
        "mtime": stat.st_mtime if stat else 0,
        "ctime": stat.st_ctime if stat else 0,
        "source": "fs_watcher",
    }


def handle_event(event: Any, *, auto_ingest: bool = True, submit_summaries: bool | None = None) -> dict[str, Any]:
    record = event_to_file_record(event)
    event_type = str(record["event_type"])
    path = record["canonical_path"]
    if event_type == "deleted":
        return process_delete(path)
    if should_ignore_path(path):
        log_event("watch", path, "ignored", "outside_authorized_scope_or_excluded")
        return {"ok": False, "decision": "ignored", "record": record}
    if not wait_until_file_stable(path):
        return {"ok": False, "decision": "not_stable", "record": record}
    row = upsert_file_record(
        canonical_path=path,
        file_type=record["file_type"],
        size=int(record["size"]),
        mtime=float(record["mtime"]),
        ctime=float(record["ctime"]),
        status="READY_FOR_PARSE",
        source="fs_watcher",
    )
    add_ingest_job(row["file_id"], "parse", reason=f"watch:{event_type}")
    log_event("watch", path, "queued", event_type, file_id=row["file_id"])
    if auto_ingest:
        return process_file(path, submit_summaries=submit_summaries)
    return {"ok": True, "decision": "queued", "record": record, "file_id": row["file_id"]}


def start_watch(*, duration: float | None = None, submit_summaries: bool | None = None) -> dict[str, Any]:
    roots = [Path(p) for p in list_allowed_roots()]
    if not roots:
        return {"ok": False, "status": "no_authorized_roots", "reason": "run ms8 absorb add <dir> first"}
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError:
        return {"ok": False, "status": "missing_dependency", "reason": "install ms8[absorb] for watchdog support"}

    class AbsorbHandler(FileSystemEventHandler):
        def on_created(self, event: Any) -> None:
            if not getattr(event, "is_directory", False):
                handle_event(event, submit_summaries=submit_summaries)

        def on_modified(self, event: Any) -> None:
            if not getattr(event, "is_directory", False):
                handle_event(event, submit_summaries=submit_summaries)

        def on_deleted(self, event: Any) -> None:
            if not getattr(event, "is_directory", False):
                handle_event(event, submit_summaries=submit_summaries)

    observer = Observer()
    handler = AbsorbHandler()
    for root in roots:
        observer.schedule(handler, str(root), recursive=True)
    observer.start()
    started_at = time.time()
    poll_scans = 0
    poll_processed = 0
    last_poll = started_at
    try:
        first_poll = _poll_authorized_roots(submit_summaries=submit_summaries)
        poll_scans += 1
        poll_processed += int(first_poll.get("processed", 0) or 0)
        if duration is None:
            while True:
                time.sleep(1)
                if time.time() - last_poll >= 5:
                    polled = _poll_authorized_roots(submit_summaries=submit_summaries)
                    poll_scans += 1
                    poll_processed += int(polled.get("processed", 0) or 0)
                    last_poll = time.time()
        else:
            deadline = started_at + max(0.0, float(duration))
            while time.time() < deadline:
                time.sleep(0.2)
                if time.time() - last_poll >= 5:
                    polled = _poll_authorized_roots(submit_summaries=submit_summaries)
                    poll_scans += 1
                    poll_processed += int(polled.get("processed", 0) or 0)
                    last_poll = time.time()
    except KeyboardInterrupt:
        logger.info("absorb watcher interrupted by user")
    finally:
        try:
            final_poll = _poll_authorized_roots(submit_summaries=submit_summaries)
            poll_scans += 1
            poll_processed += int(final_poll.get("processed", 0) or 0)
        except (OSError, ValueError, TypeError) as exc:
            log_event("watch", "", "poll_failed", str(exc))
        observer.stop()
        observer.join(timeout=5)
    return {
        "ok": True,
        "status": "stopped",
        "roots": [str(p) for p in roots],
        "duration": round(time.time() - started_at, 2),
        "poll_scans": poll_scans,
        "poll_processed": poll_processed,
    }


def stop_watch() -> dict[str, Any]:
    return {
        "ok": True,
        "status": "foreground_only",
        "reason": "ms8 absorb start runs in the foreground and stops with Ctrl-C",
        "background_service_stop": "ms8 service absorb-remove",
    }


def _poll_authorized_roots(*, submit_summaries: bool | None = None, limit: int = 100) -> dict[str, Any]:
    scan = bootstrap_authorized_roots()
    ingest = process_pending(submit_summaries=submit_summaries, limit=limit)
    return {
        "ok": bool(scan.get("ok", False)) and bool(ingest.get("ok", False)),
        "discovered": int(scan.get("discovered", 0) or 0),
        "indexed": int(scan.get("indexed", 0) or 0),
        "processed": int(ingest.get("processed", 0) or 0),
    }
