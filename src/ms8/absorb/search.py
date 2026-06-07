"""Search layer for absorbed local document chunks.

Whoosh is used when an index is available. SQLite LIKE remains the safe fallback.
Both paths only expose chunks that passed absorb governance.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .repository import SEARCHABLE_CHUNK_STATUSES, absorb_root, get_chunk, list_chunks_by_status
from .repository import search_chunks as sqlite_search_chunks

logger = logging.getLogger(__name__)


def index_dir() -> Path:
    return absorb_root() / "whoosh"


def _schema():
    from whoosh.fields import ID, KEYWORD, NUMERIC, TEXT, Schema

    return Schema(
        chunk_id=ID(stored=True, unique=True),
        file_id=ID(stored=True),
        canonical_path=ID(stored=True),
        file_type=KEYWORD(stored=True, commas=True, lowercase=True),
        status=KEYWORD(stored=True, commas=True, lowercase=True),
        risk_level=KEYWORD(stored=True, commas=True, lowercase=True),
        chunk_index=NUMERIC(stored=True),
        text=TEXT(stored=True),
    )


def _open_or_create_index():
    from whoosh import index

    root = index_dir()
    root.mkdir(parents=True, exist_ok=True)
    if index.exists_in(root):
        return index.open_dir(root)
    return index.create_in(root, _schema())


def rebuild_search_index() -> dict[str, Any]:
    """Rebuild the absorb Whoosh index from safe searchable chunks."""
    try:
        ix = _open_or_create_index()
    except ImportError as exc:
        return {"ok": False, "status": "missing_dependency", "reason": str(exc)}
    chunks = list_chunks_by_status(SEARCHABLE_CHUNK_STATUSES, limit=100_000)
    writer = ix.writer()
    try:
        for chunk in chunks:
            writer.update_document(
                chunk_id=str(chunk.get("chunk_id", "")),
                file_id=str(chunk.get("file_id", "")),
                canonical_path=str(chunk.get("canonical_path", "")),
                file_type=str(chunk.get("file_type", "")),
                status=str(chunk.get("status", "")),
                risk_level=str(chunk.get("risk_level", "")),
                chunk_index=int(chunk.get("chunk_index", 0) or 0),
                text=str(chunk.get("text_preview", "")),
            )
        writer.commit()
    except (OSError, RuntimeError, TypeError, ValueError):
        writer.cancel()
        raise
    return {"ok": True, "status": "indexed", "chunks": len(chunks), "index_dir": str(index_dir())}


def _whoosh_search(query: str, limit: int) -> list[dict[str, Any]]:
    from whoosh import index
    from whoosh.qparser import MultifieldParser, OrGroup

    root = index_dir()
    if not index.exists_in(root):
        return []
    ix = index.open_dir(root)
    parser = MultifieldParser(["text", "canonical_path"], schema=ix.schema, group=OrGroup)
    parsed = parser.parse(query)
    with ix.searcher() as searcher:
        results = searcher.search(parsed, limit=limit)
        return [
            {
                "chunk_id": str(hit.get("chunk_id", "")),
                "file_id": str(hit.get("file_id", "")),
                "canonical_path": str(hit.get("canonical_path", "")),
                "file_type": str(hit.get("file_type", "")),
                "status": str(hit.get("status", "")),
                "risk_level": str(hit.get("risk_level", "")),
                "chunk_index": int(hit.get("chunk_index", 0) or 0),
                "text_preview": str(hit.get("text", "")),
                "search_backend": "whoosh",
                "score": float(hit.score),
            }
            for hit in results
        ]


def search_chunks(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Search absorb chunks with a Whoosh-first, SQLite-fallback strategy."""
    q = query.strip()
    if not q:
        return []
    try:
        matches = _whoosh_search(q, limit)
    except ImportError as exc:
        logger.debug("absorb whoosh search unavailable: %s", exc)
        matches = []
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.warning("absorb whoosh search fallback after error: %s", exc)
        matches = []
    if matches:
        live_matches: list[dict[str, Any]] = []
        for match in matches:
            chunk = get_chunk(str(match.get("chunk_id", "") or ""))
            if not chunk:
                continue
            if str(chunk.get("status", "")) not in SEARCHABLE_CHUNK_STATUSES:
                continue
            if str(chunk.get("file_status", "")) in {"QUARANTINED", "DELETED", "ERROR", "FILTERED"}:
                continue
            chunk["search_backend"] = match.get("search_backend", "whoosh")
            chunk["score"] = match.get("score", 0.0)
            live_matches.append(chunk)
        if live_matches:
            return live_matches[:limit]
        logger.debug("absorb whoosh search returned only stale matches; using sqlite fallback")
    fallback = sqlite_search_chunks(q, limit=limit)
    for item in fallback:
        item.setdefault("search_backend", "sqlite")
    return fallback
