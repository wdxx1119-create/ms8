"""Knowledge-graph bridge for locally absorbed documents."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .repository import SEARCHABLE_CHUNK_STATUSES, list_audit_events, list_chunks_by_status, log_event


def _kg_candidates(limit: int = 50) -> list[dict[str, Any]]:
    rows = list_chunks_by_status(SEARCHABLE_CHUNK_STATUSES, limit=max(0, int(limit)))
    candidates: list[dict[str, Any]] = []
    for row in rows:
        text = str(row.get("text_preview", "") or "").strip()
        if not text or str(row.get("risk_level", "") or "").lower() != "low":
            continue
        canonical_path = str(row.get("canonical_path", "") or "")
        file_id = str(row.get("file_id", "") or "")
        chunk_id = str(row.get("chunk_id", "") or "")
        memory_ref = f"absorb:{file_id}:{chunk_id}"
        candidates.append(
            {
                "memory_ref": memory_ref,
                "source": "absorb",
                "title": Path(canonical_path).name or "Local Document Chunk",
                "content": text,
                "file_id": file_id,
                "chunk_id": chunk_id,
                "canonical_path": canonical_path,
                "risk_level": "low",
                "status": str(row.get("status", "") or ""),
            }
        )
    return candidates


def extract_absorb_knowledge_graph(
    *,
    limit: int = 50,
    apply: bool = False,
    force: bool = False,
    kg: Any | None = None,
) -> dict[str, Any]:
    """Preview or apply KG extraction for safe local document chunks.

    The bridge intentionally accepts only low-risk searchable chunks. Pending
    review, quarantined, deleted, or non-low-risk content must not reach KG.
    """
    candidates = _kg_candidates(limit=limit)
    payload: dict[str, Any] = {
        "ok": True,
        "status": "applied" if apply else "dry_run",
        "applied": bool(apply),
        "count": len(candidates),
        "planned": [
            {
                "memory_ref": item["memory_ref"],
                "chunk_id": item["chunk_id"],
                "file_id": item["file_id"],
                "canonical_path": item["canonical_path"],
                "title": item["title"],
                "risk_level": item["risk_level"],
                "status": item["status"],
            }
            for item in candidates
        ],
        "results": [],
    }
    if not apply:
        return payload

    graph = kg
    if graph is None:
        from ..engine_core.knowledge_graph import KnowledgeGraph

        graph = KnowledgeGraph()

    results: list[dict[str, Any]] = []
    for item in candidates:
        result = graph.ingest_memory(
            item["memory_ref"],
            item["content"],
            source=item["source"],
            title=item["title"],
            use_llm=False,
            force=force,
        )
        results.append(
            {
                "memory_ref": item["memory_ref"],
                "chunk_id": item["chunk_id"],
                "file_id": item["file_id"],
                "status": result.get("status", "unknown") if isinstance(result, dict) else "unknown",
                "result": result,
            }
        )
        log_event(
            "kg_extract",
            item["canonical_path"],
            str(result.get("status", "unknown") if isinstance(result, dict) else "unknown"),
            item["memory_ref"],
            file_id=item["file_id"],
        )
    payload["results"] = results
    return payload


def kg_extract_health(limit: int = 50) -> dict[str, Any]:
    """Return lightweight KG extraction health for absorb."""
    candidates = _kg_candidates(limit=limit)
    events = list_audit_events(event_type="kg_extract", limit=10_000)
    status_counts: dict[str, int] = {}
    latest_at = ""
    for event in events:
        decision = str(event.get("decision", "") or "unknown")
        status_counts[decision] = status_counts.get(decision, 0) + 1
        created_at = str(event.get("created_at", "") or "")
        if created_at > latest_at:
            latest_at = created_at
    return {
        "pending_candidates": len(candidates),
        "applied_total": len(events),
        "status_counts": status_counts,
        "latest_at": latest_at,
    }
