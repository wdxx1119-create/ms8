"""Incremental absorb processing pipeline."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .chunker import estimate_tokens, make_chunk_hash, split_text
from .governance import run_absorb_governance, submit_to_ms8_governed, write_quarantine_metadata
from .parser import MAX_FILE_BYTES, parse_document
from .repository import (
    add_chunk,
    get_file_by_hash,
    get_file_by_path,
    list_by_status,
    log_event,
    update_file_status,
    upsert_file_record,
)
from .scope import auto_submit_summaries_enabled, is_path_allowed


def calculate_hash(path: str | Path) -> str:
    p = Path(path).expanduser().resolve()
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def process_delete(path: str | Path) -> dict[str, Any]:
    row = get_file_by_path(path)
    if not row:
        return {"ok": False, "status": "not_found"}
    update_file_status(row["file_id"], "DELETED", "source_file_deleted")
    log_event("delete", row["canonical_path"], "marked_deleted", file_id=row["file_id"])
    return {"ok": True, "status": "DELETED", "file_id": row["file_id"]}


def mark_deprecated_on_delete(file_id: str) -> dict[str, Any]:
    update_file_status(file_id, "DELETED", "deprecated_source")
    return {"ok": True, "file_id": file_id}


def submit_summary_to_ms8(parsed_doc: Any, chunks: list[str], metadata: dict[str, Any]) -> dict[str, Any]:
    summary = f"Local file summary: {parsed_doc.title}. Source: {parsed_doc.source_path}. Chunks: {len(chunks)}."
    return submit_to_ms8_governed(summary, metadata)


def process_file(path: str | Path, *, submit_summaries: bool | None = None, rebuild_index: bool = True) -> dict[str, Any]:
    if submit_summaries is None:
        submit_summaries = auto_submit_summaries_enabled()
    p = Path(path).expanduser().resolve()
    if not is_path_allowed(p):
        log_event("process", str(p), "rejected", "outside_authorized_scope")
        return {"ok": False, "status": "rejected", "reason": "outside_authorized_scope"}
    if not p.exists():
        return process_delete(p)
    stat = p.stat()
    if stat.st_size > MAX_FILE_BYTES:
        row = upsert_file_record(
            canonical_path=str(p),
            file_type=p.suffix.lower(),
            size=stat.st_size,
            mtime=stat.st_mtime,
            ctime=stat.st_ctime,
            status="FILTERED",
            parse_status="skipped",
            source="ingest",
            error=f"file_too_large:{stat.st_size}>{MAX_FILE_BYTES}",
        )
        log_event("process", str(p), "filtered", "file_too_large", file_id=row["file_id"])
        return {
            "ok": False,
            "status": "FILTERED",
            "file_id": row["file_id"],
            "reason": "file_too_large",
            "size": stat.st_size,
            "max_size": MAX_FILE_BYTES,
        }
    content_hash = calculate_hash(p)
    duplicate = get_file_by_hash(content_hash)
    existing = get_file_by_path(p)
    if duplicate and (not existing or duplicate["canonical_path"] != str(p)):
        row = upsert_file_record(
            canonical_path=str(p),
            file_type=p.suffix.lower(),
            size=p.stat().st_size,
            mtime=p.stat().st_mtime,
            ctime=p.stat().st_ctime,
            content_hash=content_hash,
            status="DUPLICATE",
            source="ingest",
        )
        log_event("process", str(p), "duplicate", "content_hash_seen", file_id=row["file_id"])
        return {"ok": True, "status": "DUPLICATE", "file_id": row["file_id"]}
    parsed = parse_document(p)
    parsed_file_status = "PARSED" if parsed.parse_status == "parsed" else ("OCR_REQUIRED" if parsed.parse_status == "ocr_required" else "ERROR")
    row = upsert_file_record(
        canonical_path=str(p),
        file_type=p.suffix.lower(),
        size=p.stat().st_size,
        mtime=p.stat().st_mtime,
        ctime=p.stat().st_ctime,
        content_hash=content_hash,
        status=parsed_file_status,
        parse_status=parsed.parse_status,
        source="ingest",
        error=parsed.error,
    )
    if parsed.parse_status != "parsed":
        log_event("parse", str(p), "error", parsed.error or parsed.parse_status, file_id=row["file_id"])
        return {"ok": False, "status": parsed_file_status, "file_id": row["file_id"], "error": parsed.error}
    chunks = split_text(parsed.content_text)
    stats = {"local_indexed": 0, "pending_review": 0, "quarantined": 0, "submitted": 0}
    for idx, chunk in enumerate(chunks):
        chunk_hash = make_chunk_hash(chunk)
        gov = run_absorb_governance(chunk, {"path": str(p), "chunk_index": idx})
        decision = gov["decision"]
        if decision == "quarantine":
            write_quarantine_metadata(
                file_id=row["file_id"],
                chunk_index=idx,
                source_path=str(p),
                content_hash=content_hash,
                chunk_hash=chunk_hash,
                governance=gov,
            )
            add_chunk(
                file_id=row["file_id"],
                chunk_index=idx,
                chunk_hash=chunk_hash,
                text_preview=gov.get("redacted_preview", ""),
                token_count=estimate_tokens(chunk),
                status="QUARANTINED",
                risk_level=gov["risk_level"],
            )
            stats["quarantined"] += 1
        elif decision == "pending_review":
            add_chunk(
                file_id=row["file_id"],
                chunk_index=idx,
                chunk_hash=chunk_hash,
                text_preview=gov.get("redacted_preview", ""),
                token_count=estimate_tokens(chunk),
                status="PENDING_REVIEW",
                risk_level=gov["risk_level"],
            )
            stats["pending_review"] += 1
        else:
            add_chunk(
                file_id=row["file_id"],
                chunk_index=idx,
                chunk_hash=chunk_hash,
                text_preview=gov.get("redacted_preview", ""),
                token_count=estimate_tokens(chunk),
                status="LOCAL_INDEXED",
                risk_level="low",
            )
            stats["local_indexed"] += 1
    final_status = "QUARANTINED" if stats["quarantined"] else ("PENDING_REVIEW" if stats["pending_review"] else "LOCAL_INDEXED")
    update_file_status(row["file_id"], final_status)
    if submit_summaries and stats["local_indexed"]:
        submit = submit_summary_to_ms8(parsed, chunks, {"source_path": str(p), "absorb_file_id": row["file_id"]})
        if submit.get("ok"):
            stats["submitted"] += 1
            update_file_status(row["file_id"], "SUBMITTED_TO_MS8")
    log_event("process", str(p), final_status, "ingest_complete", file_id=row["file_id"])
    if rebuild_index and (stats["local_indexed"] or stats["submitted"]):
        from .search import rebuild_search_index

        rebuild_search_index()
    return {"ok": True, "status": final_status, "file_id": row["file_id"], **stats}


def process_pending(*, submit_summaries: bool | None = None, limit: int = 100) -> dict[str, Any]:
    if submit_summaries is None:
        submit_summaries = auto_submit_summaries_enabled()
    rows = list_by_status(("DISCOVERED", "CHANGED", "READY_FOR_PARSE"))
    results = [process_file(row["canonical_path"], submit_summaries=submit_summaries, rebuild_index=False) for row in rows[:limit]]
    if any(bool(result.get("local_indexed") or result.get("submitted")) for result in results):
        from .search import rebuild_search_index

        rebuild_search_index()
    return {"ok": True, "processed": len(results), "results": results}
