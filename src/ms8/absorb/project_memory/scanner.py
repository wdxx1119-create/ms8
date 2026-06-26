"""Scanner for absorb project-memory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..chunker import estimate_tokens, make_chunk_hash, split_text
from .parser import parse_document
from .repository import connection, init_repository, mark_deleted_missing, replace_chunks, stats, upsert_file
from .scope import mark_index_stale

ALLOWED_SUFFIXES = {".md", ".txt", ".rst", ".yaml", ".yml", ".toml", ".json", ".cfg", ".ini", ".py", ".pdf", ".docx"}
SPECIAL_NAMES = ("README", "LICENSE", "CHANGELOG")
EXCLUDED_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    "vendor",
    ".cache",
    ".idea",
    ".vscode",
}
MAX_BYTES = 2_000_000


def _is_allowed(path: Path) -> tuple[bool, str]:
    # Never follow project-local links to content outside the authorized root.
    # A symlink can otherwise make an apparently in-scope path expose arbitrary
    # local files to parsing and memory submission.
    if path.is_symlink():
        return False, "symlink"
    if any(part in EXCLUDED_DIRS for part in path.parts):
        return False, "excluded_pattern"
    if path.name.startswith("."):
        return False, "hidden"
    if path.stat().st_size > MAX_BYTES:
        return False, "too_large"
    if path.suffix.lower() in ALLOWED_SUFFIXES:
        return True, ""
    upper = path.name.upper()
    if any(upper.startswith(prefix) for prefix in SPECIAL_NAMES):
        return True, ""
    return False, "unsupported"


def scan_project(*, project_name: str, project_root: Path, db_path: Path, index_state_path: Path) -> dict[str, Any]:
    project_root = project_root.expanduser()
    if not project_root.exists() or not project_root.is_dir():
        return {
            "ok": False,
            "name": project_name,
            "status": "invalid_project_root",
            "error": "project_root_missing_or_not_directory",
            "project_root": str(project_root),
            "next_actions": ["verify the registered project path and run project-memory init again"],
        }

    project_root = project_root.resolve()
    init_repository(db_path)
    files_found = 0
    files_scanned = 0
    files_unchanged = 0
    files_skipped = 0
    chunks_created = 0
    skipped_reasons = {
        "binary": 0,
        "too_large": 0,
        "excluded_pattern": 0,
        "hidden": 0,
        "unsupported": 0,
        "symlink": 0,
    }
    seen_relative_paths: set[str] = set()
    changed_paths: set[str] = set()
    deleted_paths: list[str] = []
    with connection(db_path) as conn:
        for path in sorted(project_root.rglob("*")):
            if not path.is_file():
                continue
            files_found += 1
            allowed, reason = _is_allowed(path)
            if not allowed:
                files_skipped += 1
                skipped_reasons[reason] = int(skipped_reasons.get(reason, 0)) + 1
                continue
            rel = path.relative_to(project_root).as_posix()
            seen_relative_paths.add(rel)
            existing = conn.execute(
                "SELECT content_hash, mtime, size, status FROM file_records WHERE relative_path=?",
                (rel,),
            ).fetchone()
            current_mtime = float(path.stat().st_mtime)
            current_size = int(path.stat().st_size)
            if (
                existing
                and abs(float(existing["mtime"] or 0.0) - current_mtime) < 1e-9
                and int(existing["size"] or 0) == current_size
                and str(existing["status"] or "") != "DELETED"
            ):
                files_unchanged += 1
                continue
            parsed = parse_document(path)
            current_hash = str(parsed.content_hash or "")
            file_id = upsert_file(
                conn,
                relative_path=rel,
                absolute_path=str(path),
                file_type=parsed.file_type or path.suffix.lower(),
                title=parsed.title or path.stem,
                size=current_size,
                mtime=current_mtime,
                content_hash=current_hash,
                parse_status=str(parsed.parse_status or "error"),
                status="INDEXED" if parsed.parse_status in {"parsed", "empty"} else "ERROR",
            )
            chunk_payloads: list[dict[str, Any]] = []
            for index, text in enumerate(split_text(parsed.content_text, max_tokens=512, overlap_tokens=64)):
                chunk_payloads.append(
                    {
                        "chunk_index": index,
                        "chunk_hash": make_chunk_hash(text),
                        "token_count": estimate_tokens(text),
                        "text": text,
                    }
                )
            chunks_created += replace_chunks(conn, file_id, chunk_payloads)
            files_scanned += 1
            changed_paths.add(rel)
        before_rows = conn.execute("SELECT relative_path, status FROM file_records").fetchall()
        previous_active = {str(row["relative_path"]) for row in before_rows if str(row["status"] or "") != "DELETED"}
        deleted_count = mark_deleted_missing(conn, seen_relative_paths)
        if deleted_count:
            deleted_paths = sorted(previous_active - seen_relative_paths)
    index_state = mark_index_stale(
        index_state_path,
        content_db_ready=True,
        changed_files_pending=len(changed_paths) + len(deleted_paths),
        changed_paths=sorted(changed_paths),
        deleted_paths=deleted_paths,
    )
    current = stats(db_path)
    return {
        "ok": True,
        "name": project_name,
        "status": "scan_complete",
        "content_db_ready": True,
        "search_index_ready": False,
        "index_status": str(index_state.get("status", "stale")),
        "files_found": files_found,
        "files_scanned": files_scanned,
        "files_unchanged": files_unchanged,
        "files_skipped": files_skipped,
        "chunks_created": chunks_created,
        "changed_paths": sorted(changed_paths),
        "deleted_paths": deleted_paths,
        "skipped_reasons": skipped_reasons,
        "current_stats": current,
        "next_actions": [
            f"ms8 absorb project-memory index --name {project_name}",
            f"ms8 absorb project-memory build --name {project_name}",
        ],
    }
