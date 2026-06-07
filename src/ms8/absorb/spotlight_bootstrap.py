"""Authorized root discovery for absorb."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .parser import SUPPORTED_TYPES
from .repository import add_ingest_job, log_event, upsert_file_record
from .scope import is_path_allowed, list_allowed_roots

SUPPORTED_EXTENSIONS = SUPPORTED_TYPES


def _candidate_ok(path: Path) -> bool:
    if not path.is_file():
        return False
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        return False
    return is_path_allowed(path)


def get_mdls_metadata(path: str | Path) -> dict[str, Any]:
    p = Path(path).expanduser().resolve()
    try:
        proc = subprocess.run(["mdls", "-name", "kMDItemContentType", str(p)], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    return {"raw": proc.stdout.strip()} if proc.returncode == 0 else {}


def discover_with_spotlight(root_path: str | Path) -> list[Path]:
    root = Path(root_path).expanduser().resolve()
    query = " || ".join([f"kMDItemFSName == '*{ext}'" for ext in sorted(SUPPORTED_EXTENSIONS)])
    try:
        proc = subprocess.run(["mdfind", "-onlyin", str(root), query], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    return [Path(line).expanduser().resolve() for line in proc.stdout.splitlines() if line.strip()]


def discover_with_walk(root_path: str | Path) -> list[Path]:
    root = Path(root_path).expanduser().resolve()
    found: list[Path] = []
    for path in root.rglob("*"):
        if _candidate_ok(path):
            found.append(path)
    return found


def bootstrap_authorized_roots() -> dict[str, Any]:
    discovered = 0
    indexed = 0
    skipped = 0
    for root in list_allowed_roots():
        candidates = discover_with_spotlight(root)
        source = "spotlight"
        if not candidates:
            candidates = discover_with_walk(root)
            source = "walk"
        discovered += len(candidates)
        for path in candidates:
            if not _candidate_ok(path):
                skipped += 1
                continue
            stat = path.stat()
            row = upsert_file_record(
                canonical_path=str(path),
                file_type=path.suffix.lower(),
                size=stat.st_size,
                mtime=stat.st_mtime,
                ctime=stat.st_ctime,
                status="READY_FOR_PARSE",
                source=source,
            )
            add_ingest_job(row["file_id"], "parse", "bootstrap")
            log_event("discover", str(path), "indexed", "bootstrap", file_id=row["file_id"])
            indexed += 1
    return {"ok": True, "discovered": discovered, "indexed": indexed, "skipped": skipped}
