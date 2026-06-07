"""Review and submission helpers for absorb chunks."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..record_policy import is_valid_status_transition

from .governance import submit_to_ms8_governed
from .repository import (
    get_chunk,
    list_audit_events,
    list_chunks_by_status,
    log_event,
    update_chunk_status,
    update_file_status,
)
from .scope import auto_write_tier

DEFAULT_DAILY_CHUNK_CAP = 20


def list_review_chunks(limit: int = 50) -> dict[str, Any]:
    return {
        "ok": True,
        "pending_review": list_chunks_by_status(("PENDING_REVIEW",), limit=limit),
        "quarantine": list_chunks_by_status(("QUARANTINED",), limit=limit),
    }


def approve_chunk(chunk_id: str, *, submit: bool = False, rebuild_index: bool = True) -> dict[str, Any]:
    chunk = get_chunk(chunk_id)
    if not chunk:
        return {"ok": False, "status": "not_found", "chunk_id": chunk_id}
    if chunk["status"] not in {"PENDING_REVIEW", "LOCAL_INDEXED"}:
        return {"ok": False, "status": "not_reviewable", "chunk_id": chunk_id, "current_status": chunk["status"]}
    if chunk["status"] == "LOCAL_INDEXED" and not submit:
        return {"ok": True, "status": "already_approved", "chunk_id": chunk_id}
    update_chunk_status(chunk_id, "LOCAL_INDEXED")
    update_file_status(chunk["file_id"], "LOCAL_INDEXED", "chunk_approved")
    log_event("review", chunk["canonical_path"], "approved", chunk_id, file_id=chunk["file_id"])
    payload: dict[str, Any] = {"ok": True, "status": "approved", "chunk_id": chunk_id}
    if submit:
        payload["submit"] = submit_chunk(chunk_id)
    if rebuild_index:
        from .search import rebuild_search_index

        rebuild_search_index()
    return payload


def reject_chunk(chunk_id: str, reason: str = "user_rejected", *, rebuild_index: bool = True) -> dict[str, Any]:
    chunk = get_chunk(chunk_id)
    if not chunk:
        return {"ok": False, "status": "not_found", "chunk_id": chunk_id}
    update_chunk_status(chunk_id, "MS8_REJECTED")
    log_event("review", chunk["canonical_path"], "rejected", reason, file_id=chunk["file_id"])
    if rebuild_index:
        from .search import rebuild_search_index

        rebuild_search_index()
    return {"ok": True, "status": "rejected", "chunk_id": chunk_id}


def restore_rejected_chunk(chunk_id: str, *, rebuild_index: bool = True) -> dict[str, Any]:
    chunk = get_chunk(chunk_id)
    if not chunk:
        return {"ok": False, "status": "not_found", "chunk_id": chunk_id}
    if chunk["status"] != "MS8_REJECTED":
        return {"ok": False, "status": "not_restorable", "chunk_id": chunk_id, "current_status": chunk["status"]}
    update_chunk_status(chunk_id, "PENDING_REVIEW")
    update_file_status(chunk["file_id"], "PENDING_REVIEW", "chunk_restored_for_review")
    log_event("review", chunk["canonical_path"], "restored", chunk_id, file_id=chunk["file_id"])
    if rebuild_index:
        from .search import rebuild_search_index

        rebuild_search_index()
    return {"ok": True, "status": "restored", "chunk_id": chunk_id}


def submit_chunk(chunk_id: str) -> dict[str, Any]:
    chunk = get_chunk(chunk_id)
    if not chunk:
        return {"ok": False, "status": "not_found", "chunk_id": chunk_id}
    if chunk["status"] not in {"LOCAL_INDEXED", "PENDING_REVIEW"}:
        return {"ok": False, "status": "not_submittable", "chunk_id": chunk_id, "current_status": chunk["status"]}
    summary = (
        f"Local file memory: {chunk['text_preview']} "
        f"(source: {chunk['canonical_path']}, chunk: {chunk['chunk_index']})"
    )
    submitted = submit_to_ms8_governed(
        summary,
        {"source_path": chunk["canonical_path"], "absorb_file_id": chunk["file_id"], "absorb_chunk_id": chunk_id},
    )
    if submitted.get("ok"):
        update_chunk_status(chunk_id, "SUBMITTED_TO_MS8", submitted=True)
        update_file_status(chunk["file_id"], "SUBMITTED_TO_MS8", "chunk_submitted")
        log_event("review", chunk["canonical_path"], "submitted_to_ms8", chunk_id, file_id=chunk["file_id"])
        from .search import rebuild_search_index

        rebuild_search_index()
    return {"ok": bool(submitted.get("ok")), "status": "submitted" if submitted.get("ok") else "submit_failed", "result": submitted}


def _utc_date_prefix() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _auto_submitted_today() -> int:
    events = list_audit_events(event_type="auto_write", decision="submitted_to_ms8", limit=10_000)
    today = _utc_date_prefix()
    return sum(1 for event in events if str(event.get("created_at", "")).startswith(today))


def _parse_event_payload(event: dict[str, Any]) -> dict[str, Any]:
    try:
        payload = json.loads(str(event.get("reason", "") or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _rollback_summary(planned: list[dict[str, Any]], not_reversible: list[dict[str, Any]]) -> dict[str, Any]:
    files = sorted({str(item.get("source_path", "")) for item in planned if item.get("source_path")})
    return {
        "records_to_revoke": len(planned),
        "files_affected": len(files),
        "not_reversible": len(not_reversible),
        "affected_files": files[:10],
        "message": (
            f"Will revoke {len(planned)} auto-written memory record(s) from "
            f"{len(files)} local file(s)."
        ),
    }


def _event_is_within_hours(event: dict[str, Any], since_hours: int) -> bool:
    raw = str(event.get("created_at", "") or "")
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    age_seconds = (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds()
    return age_seconds <= max(0, int(since_hours)) * 3600


def _approved_chunk_ids() -> set[str]:
    events = list_audit_events(event_type="review", decision="approved", limit=10_000)
    return {str(event.get("reason", "")) for event in events if str(event.get("reason", "")).startswith("chunk_")}


def _auto_write_candidates(tier: str, limit: int) -> list[dict[str, Any]]:
    normalized = str(tier or "").upper()
    if normalized == "LOW_RISK_CHUNKS":
        chunks = list_chunks_by_status(("LOCAL_INDEXED",), limit=limit)
        return [chunk for chunk in chunks if str(chunk.get("risk_level", "")).lower() == "low"]
    if normalized == "REVIEWED_ONLY":
        approved = _approved_chunk_ids()
        chunks = list_chunks_by_status(("LOCAL_INDEXED",), limit=limit)
        return [chunk for chunk in chunks if str(chunk.get("chunk_id", "")) in approved]
    return []


def auto_submit_by_tier(*, limit: int = 20, daily_cap: int = DEFAULT_DAILY_CHUNK_CAP, apply: bool = False) -> dict[str, Any]:
    tier = auto_write_tier()
    if tier in {"OFF", "SUMMARY_ONLY"}:
        return {"ok": True, "status": "noop", "auto_write_tier": tier, "reason": "tier_does_not_auto_submit_chunks"}
    used = _auto_submitted_today()
    remaining = max(0, int(daily_cap) - used)
    if remaining <= 0:
        return {"ok": True, "status": "cap_reached", "auto_write_tier": tier, "daily_cap": int(daily_cap), "used_today": used, "planned": []}
    candidates = _auto_write_candidates(tier, min(int(limit), remaining))
    planned = [str(chunk.get("chunk_id", "")) for chunk in candidates]
    if not apply:
        return {
            "ok": True,
            "status": "dry_run",
            "auto_write_tier": tier,
            "daily_cap": int(daily_cap),
            "used_today": used,
            "remaining_today": remaining,
            "count": len(planned),
            "chunk_ids": planned,
        }
    results: list[dict[str, Any]] = []
    for chunk in candidates:
        chunk_id = str(chunk.get("chunk_id", ""))
        result = submit_chunk(chunk_id)
        results.append(result)
        if result.get("ok"):
            record = result.get("result", {}).get("record", {}) if isinstance(result.get("result"), dict) else {}
            reason = json.dumps(
                {"chunk_id": chunk_id, "tier": tier, "record_id": str(record.get("id", "")), "source_system": "absorb"},
                ensure_ascii=False,
            )
            log_event("auto_write", str(chunk.get("canonical_path", "")), "submitted_to_ms8", reason, file_id=str(chunk.get("file_id", "")))
    return {
        "ok": all(bool(item.get("ok")) for item in results),
        "status": "applied",
        "auto_write_tier": tier,
        "daily_cap": int(daily_cap),
        "used_today": used,
        "count": len(results),
        "results": results,
    }


def _rewrite_revoked_records(records_file: Path, record_ids: set[str], *, source_system: str = "absorb") -> dict[str, Any]:
    if not record_ids:
        return {"revoked": 0, "missing": []}
    if not records_file.exists():
        return {"revoked": 0, "missing": sorted(record_ids)}
    seen: set[str] = set()
    revoked = 0
    lines: list[str] = []
    for raw in records_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            lines.append(raw)
            continue
        if not isinstance(row, dict):
            lines.append(raw)
            continue
        rid = str(row.get("id", ""))
        if rid in record_ids:
            seen.add(rid)
            raw_meta = row.get("meta")
            meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
            if str(meta.get("source_system", "") or "") != source_system:
                lines.append(json.dumps(row, ensure_ascii=False))
                continue
            old_status = str(row.get("status", "accepted") or "accepted")
            if is_valid_status_transition(old_status, "revoked"):
                row["status"] = "revoked"
                row["can_recall"] = False
                row["can_inject"] = False
                meta = row.setdefault("meta", {})
                if isinstance(meta, dict):
                    meta["revoked_by"] = "absorb_auto_write_rollback"
                    meta["revoked_at"] = datetime.now(timezone.utc).isoformat()
                revoked += 1
        lines.append(json.dumps(row, ensure_ascii=False) if isinstance(row, dict) else raw)
    tmp = records_file.with_suffix(records_file.suffix + ".absorb_rollback_tmp")
    tmp.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    tmp.replace(records_file)
    return {"revoked": revoked, "missing": sorted(record_ids - seen)}


def rollback_auto_writes(*, since_hours: int = 1, apply: bool = False, limit: int = 100, source_system: str = "absorb") -> dict[str, Any]:
    events = [
        event
        for event in list_audit_events(event_type="auto_write", decision="submitted_to_ms8", limit=10_000)
        if _event_is_within_hours(event, since_hours)
    ][: int(limit)]
    planned: list[dict[str, Any]] = []
    not_reversible: list[dict[str, Any]] = []
    for event in events:
        payload = _parse_event_payload(event)
        if str(payload.get("source_system", "absorb") or "absorb") != source_system:
            continue
        record_id = str(payload.get("record_id", "") or "")
        chunk_id = str(payload.get("chunk_id", "") or "")
        item = {
            "event_id": str(event.get("event_id", "")),
            "record_id": record_id,
            "chunk_id": chunk_id,
            "tier": str(payload.get("tier", "")),
            "source_system": str(payload.get("source_system", "absorb") or "absorb"),
            "created_at": str(event.get("created_at", "")),
            "source_path": str(event.get("path", "")),
        }
        if record_id:
            planned.append(item)
        else:
            not_reversible.append(item)
    if not apply:
        return {
            "ok": True,
            "status": "dry_run",
            "since_hours": int(since_hours),
            "source_system": source_system,
            "count": len(planned),
            "summary": _rollback_summary(planned, not_reversible),
            "planned": planned,
            "not_reversible": not_reversible,
        }
    from ..runtime import ensure_runtime_dirs

    record_ids = {item["record_id"] for item in planned if item.get("record_id")}
    result = _rewrite_revoked_records(ensure_runtime_dirs()["memories"], record_ids, source_system=source_system)
    for item in planned:
        chunk_id = str(item.get("chunk_id", ""))
        if chunk_id:
            update_chunk_status(chunk_id, "LOCAL_INDEXED")
        log_event("auto_write", "", "rollback_revoked", json.dumps(item, ensure_ascii=False))
    from .search import rebuild_search_index

    rebuild_search_index()
    return {
        "ok": True,
        "status": "applied",
        "since_hours": int(since_hours),
        "source_system": source_system,
        "count": len(planned),
        "summary": _rollback_summary(planned, not_reversible),
        "record_result": result,
        "not_reversible": not_reversible,
    }


def _review_candidates(*, risk: str = "", limit: int = 50) -> list[dict[str, Any]]:
    chunks = list_chunks_by_status(("PENDING_REVIEW",), limit=limit)
    if risk:
        expected = risk.strip().lower()
        chunks = [chunk for chunk in chunks if str(chunk.get("risk_level", "")).lower() == expected]
    return chunks


def approve_all(*, risk: str = "", limit: int = 50, apply: bool = False, submit: bool = False) -> dict[str, Any]:
    candidates = _review_candidates(risk=risk, limit=limit)
    planned = [str(chunk["chunk_id"]) for chunk in candidates]
    if not apply:
        return {"ok": True, "status": "dry_run", "action": "approve_all", "count": len(planned), "chunk_ids": planned}
    results = [approve_chunk(chunk_id, submit=submit, rebuild_index=False) for chunk_id in planned]
    from .search import rebuild_search_index

    rebuild_search_index()
    return {"ok": all(bool(item.get("ok")) for item in results), "status": "applied", "action": "approve_all", "count": len(results), "results": results}


def reject_all(*, reason: str = "bulk_rejected", risk: str = "", limit: int = 50, apply: bool = False) -> dict[str, Any]:
    candidates = _review_candidates(risk=risk, limit=limit)
    planned = [str(chunk["chunk_id"]) for chunk in candidates]
    if not apply:
        return {"ok": True, "status": "dry_run", "action": "reject_all", "count": len(planned), "chunk_ids": planned, "reason": reason}
    results = [reject_chunk(chunk_id, reason=reason, rebuild_index=False) for chunk_id in planned]
    from .search import rebuild_search_index

    rebuild_search_index()
    return {"ok": all(bool(item.get("ok")) for item in results), "status": "applied", "action": "reject_all", "count": len(results), "results": results}


def export_review_items(*, limit: int = 100, include_quarantine: bool = False) -> dict[str, Any]:
    statuses = ("PENDING_REVIEW", "QUARANTINED") if include_quarantine else ("PENDING_REVIEW",)
    items = list_chunks_by_status(statuses, limit=limit)
    return {"ok": True, "status": "exported", "count": len(items), "items": items}
