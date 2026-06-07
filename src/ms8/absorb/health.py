"""Health summary for absorb."""

from __future__ import annotations

from typing import Any

from .repository import SEARCHABLE_CHUNK_STATUSES, count_status, list_chunks_by_status, repository_integrity
from .scope import list_allowed_roots, load_absorb_config


def absorb_health_summary() -> dict[str, Any]:
    counts = count_status()
    pending = len(list_chunks_by_status(("PENDING_REVIEW",), limit=1000))
    quarantine = len(list_chunks_by_status(("QUARANTINED",), limit=1000))
    cfg = load_absorb_config()
    from .kg import kg_extract_health

    kg = kg_extract_health()
    repo = repository_integrity()
    chunks = counts.get("chunks", {}) if isinstance(counts.get("chunks", {}), dict) else {}
    searchable = sum(int(chunks.get(status, 0) or 0) for status in SEARCHABLE_CHUNK_STATUSES)
    index_consistency = _index_consistency(searchable)
    risk = "green"
    if quarantine or repo.get("events_growth_risk") == "yellow" or not bool(repo.get("db_writable", False)):
        risk = "yellow"
    if (
        pending > 20
        or quarantine > 10
        or repo.get("events_growth_risk") == "red"
        or repo.get("integrity_check") not in {"", "ok"}
        or index_consistency.get("risk") == "red"
    ):
        risk = "red"
    return {
        "ok": True,
        "risk": risk,
        "authorized_roots": len(list_allowed_roots()),
        "auto_submit_summaries": bool(cfg.get("auto_submit_summaries", False)),
        "auto_write_tier": str(cfg.get("auto_write_tier", "OFF")),
        "files": counts.get("files", {}),
        "chunks": counts.get("chunks", {}),
        "pending_review": pending,
        "quarantine": quarantine,
        "kg_extract": kg,
        "repository": repo,
        "index_consistency": index_consistency,
    }


def _index_consistency(searchable_chunks: int) -> dict[str, Any]:
    try:
        from .search import index_dir

        root = index_dir()
        exists = root.exists()
        risk = "green"
        if searchable_chunks > 0 and not exists:
            risk = "red"
        return {"exists": exists, "path": str(root), "searchable_chunks": searchable_chunks, "risk": risk}
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        return {"exists": False, "path": "", "searchable_chunks": searchable_chunks, "risk": "red", "error": str(exc)}
