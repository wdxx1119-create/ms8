"""Search layer for absorb project-memory."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .repository import active_chunks, active_chunks_for_paths, get_chunk_by_id, sqlite_search
from .scope import load_index_state, load_registry, mark_index_degraded, mark_index_ready, project_dir_paths


def _schema():
    from whoosh.fields import ID, KEYWORD, NUMERIC, TEXT, Schema

    return Schema(
        chunk_id=ID(stored=True, unique=True),
        file_id=ID(stored=True),
        relative_path=ID(stored=True),
        file_type=KEYWORD(stored=True, lowercase=True),
        chunk_index=NUMERIC(stored=True),
        text=TEXT(stored=True),
    )


def _ensure_index(whoosh_dir: Path):
    from whoosh import index

    whoosh_dir.mkdir(parents=True, exist_ok=True)
    if index.exists_in(whoosh_dir):
        return index.open_dir(whoosh_dir)
    return index.create_in(whoosh_dir, _schema())


def _update_documents(writer, chunks: list[dict[str, Any]]) -> None:
    for chunk in chunks:
        writer.update_document(
            chunk_id=str(chunk.get("chunk_id", "")),
            file_id=str(chunk.get("file_id", "")),
            relative_path=str(chunk.get("relative_path", "")),
            file_type=str(chunk.get("file_type", "")),
            chunk_index=int(chunk.get("chunk_index", 0) or 0),
            text=str(chunk.get("text", "")),
        )


def rebuild_search_index(db: Path, whoosh_dir: Path, index_state_path: Path, *, full_rebuild: bool = True) -> dict[str, Any]:
    try:
        __import__("whoosh.index")
    except ImportError as exc:
        state = mark_index_degraded(index_state_path, changed_files_pending=0, error=f"missing_dependency:{exc}")
        return {"ok": False, "status": "missing_dependency", "reason": str(exc), "index_state": state}
    state = load_index_state(index_state_path)
    changed_paths = [str(x) for x in state.get("changed_paths", []) if isinstance(x, str) and x]
    deleted_paths = [str(x) for x in state.get("deleted_paths", []) if isinstance(x, str) and x]
    incremental = (
        not full_rebuild
        and whoosh_dir.exists()
        and bool(state.get("last_index_at", ""))
    )
    if full_rebuild and whoosh_dir.exists():
        shutil.rmtree(whoosh_dir, ignore_errors=True)
    ix = _ensure_index(whoosh_dir)
    writer = ix.writer()
    try:
        if incremental:
            for rel in deleted_paths:
                writer.delete_by_term("relative_path", rel)
            for rel in changed_paths:
                writer.delete_by_term("relative_path", rel)
            _update_documents(writer, active_chunks_for_paths(db, changed_paths))
        else:
            _update_documents(writer, active_chunks(db))
        writer.commit()
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        writer.cancel()
        state = mark_index_degraded(
            index_state_path,
            changed_files_pending=len(changed_paths) + len(deleted_paths),
            changed_paths=changed_paths,
            deleted_paths=deleted_paths,
            error=str(exc),
        )
        return {"ok": False, "status": "index_build_failed", "reason": str(exc), "index_state": state}
    state = mark_index_ready(index_state_path, full_rebuild=full_rebuild or not incremental, changed_files_pending=0)
    return {
        "ok": True,
        "status": "indexed",
        "index_mode": "incremental" if incremental else "full_rebuild",
        "index_dir": str(whoosh_dir),
        "index_state": state,
        "updated_paths": changed_paths,
        "deleted_paths": deleted_paths,
    }


def _whoosh_search(whoosh_dir: Path, query: str, limit: int) -> list[dict[str, Any]]:
    from whoosh import index
    from whoosh.qparser import MultifieldParser, OrGroup

    if not index.exists_in(whoosh_dir):
        return []
    ix = index.open_dir(whoosh_dir)
    parser = MultifieldParser(["text", "relative_path"], schema=ix.schema, group=OrGroup)
    parsed = parser.parse(query)
    with ix.searcher() as searcher:
        hits = searcher.search(parsed, limit=limit)
        return [
            {
                "chunk_id": str(hit.get("chunk_id", "")),
                "relative_path": str(hit.get("relative_path", "")),
                "file_type": str(hit.get("file_type", "")),
                "chunk_index": int(hit.get("chunk_index", 0) or 0),
                "score": float(hit.score),
                "search_backend": "whoosh",
            }
            for hit in hits
        ]


def search_chunks(db: Path, whoosh_dir: Path, query: str, limit: int = 10, index_state_path: Path | None = None) -> list[dict[str, Any]]:
    q = str(query or "").strip()
    if not q:
        return []
    index_ready = True
    if index_state_path is not None:
        state = load_index_state(index_state_path)
        index_ready = bool(state.get("search_index_ready", False))
    try:
        hits = _whoosh_search(whoosh_dir, q, int(limit)) if index_ready else []
    except (ImportError, OSError, RuntimeError, TypeError, ValueError):
        hits = []
    matches: list[dict[str, Any]] = []
    for hit in hits:
        row = get_chunk_by_id(db, str(hit.get("chunk_id", "")))
        if not row:
            continue
        row["score"] = hit.get("score", 0.0)
        row["search_backend"] = hit.get("search_backend", "whoosh")
        matches.append(row)
    if matches:
        return matches[:limit]
    return sqlite_search(db, q, limit=int(limit))


def search_registered_projects(query: str, limit: int = 10, name: str | None = None) -> list[dict[str, Any]]:
    q = str(query or "").strip()
    if not q:
        return []
    registry = load_registry()
    projects = registry.get("projects", {})
    if not isinstance(projects, dict) or not projects:
        return []

    selected: list[tuple[str, dict[str, Any]]] = []
    if name:
        item = projects.get(name)
        if isinstance(item, dict):
            selected.append((str(name), item))
    else:
        selected = [
            (str(project_name), item)
            for project_name, item in projects.items()
            if isinstance(item, dict)
        ]

    rows: list[dict[str, Any]] = []
    for project_name, _item in selected:
        paths = project_dir_paths(project_name)
        db_path = paths["db_path"]
        if not db_path.exists():
            continue
        matches = search_chunks(
            db_path,
            paths["whoosh_dir"],
            q,
            limit=max(1, int(limit)),
            index_state_path=paths["index_state_path"],
        )
        for match in matches:
            row = dict(match)
            row["project_name"] = project_name
            row["source_system"] = "project_memory"
            rows.append(row)

    rows.sort(
        key=lambda item: (
            float(item.get("score", 0.0) or 0.0),
            str(item.get("project_name", "")),
            str(item.get("relative_path", "")),
        ),
        reverse=True,
    )

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        key = f"{row.get('project_name','')}::{row.get('chunk_id','') or row.get('relative_path','')}::{row.get('chunk_index',0)}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
        if len(deduped) >= int(limit):
            break
    return deduped
