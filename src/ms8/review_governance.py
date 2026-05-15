from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .record_policy import is_valid_status_transition


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            obj = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + ("\n" if rows else "")
    path.write_text(payload, encoding="utf-8")


def _sync_index_categories(index_file: Path, mem_by_id: dict[str, dict[str, Any]]) -> int:
    if not index_file.exists():
        return 0
    try:
        raw = json.loads(index_file.read_text(encoding="utf-8") or "[]")
    except (json.JSONDecodeError, OSError):
        return 0
    touched = 0

    def _apply(items: list[dict[str, Any]]) -> int:
        nonlocal touched
        local = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id", "") or item.get("meta", {}).get("id", ""))
            if not item_id:
                continue
            rec = mem_by_id.get(item_id)
            if not isinstance(rec, dict):
                continue
            rec_cat = str(rec.get("category", "") or "")
            if rec_cat and str(item.get("category", "")) != rec_cat:
                item["category"] = rec_cat
                local += 1
        touched += local
        return local

    if isinstance(raw, list):
        _apply(raw)
    elif isinstance(raw, dict):
        for key in ("items", "hot_items", "cold_items"):
            arr = raw.get(key, [])
            if isinstance(arr, list):
                _apply(arr)
    else:
        return 0

    index_file.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return touched


def _parse_dt(text: str) -> datetime | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def archive_and_sync_review_queue(
    *,
    queue_file: Path,
    records_file: Path,
    archive_dir: Path,
    report_file: Path,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    rows = _read_jsonl(queue_file)
    if not rows:
        empty_report = {
            "at": _utc_now(),
            "status": "skipped",
            "reason": "queue_empty_or_missing",
            "queue_file": str(queue_file),
        }
        report_file.parent.mkdir(parents=True, exist_ok=True)
        report_file.write_text(json.dumps(empty_report, ensure_ascii=False, indent=2), encoding="utf-8")
        return empty_report

    archive_dir.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%d")
    archive_file = archive_dir / f"review_queue_archive_{stamp}.jsonl"
    _write_jsonl(archive_file, rows)

    mem_rows = _read_jsonl(records_file)
    mem_by_id = {str(r.get("id", "")): r for r in mem_rows if str(r.get("id", ""))}
    pending: list[dict[str, Any]] = []
    orphan = 0
    synced = 0
    rejected = 0
    accepted = 0
    relabeled = 0
    invalid_decision = 0
    oldest_pending_hours = 0.0
    risk_counter: Counter[str] = Counter()
    cat_counter: Counter[str] = Counter()
    orphan_ids: list[str] = []
    invalid_decisions: Counter[str] = Counter()

    for item in rows:
        decision = str(item.get("decision", "pending")).strip().lower() or "pending"
        memory_id = str(item.get("memory_id", "")).strip()
        risk = str(item.get("risk_level", "normal") or "normal")
        cat = str(item.get("category", "unknown") or "unknown")
        risk_counter[risk] += 1
        cat_counter[cat] += 1
        if decision == "pending":
            pending.append(item)
            dt = _parse_dt(str(item.get("created_at", "")))
            if dt is not None:
                age_h = (now - dt).total_seconds() / 3600.0
                if age_h > oldest_pending_hours:
                    oldest_pending_hours = age_h
            continue

        row = mem_by_id.get(memory_id)
        if row is None:
            orphan += 1
            if memory_id:
                orphan_ids.append(memory_id)
            continue

        if decision in {"accepted", "approve", "approved", "verified"}:
            conf = float(item.get("confidence", 0.0) or 0.0)
            target_status = "verified" if conf >= 0.9 else "accepted"
            old_status = str(row.get("status", "") or "")
            if not is_valid_status_transition(old_status, target_status):
                target_status = "pending_review"
            row["status"] = target_status
            accepted += 1
            synced += 1
        elif decision in {"rejected", "reject", "deny"}:
            target_status = "revoked" if risk != "high" else "quarantined"
            old_status = str(row.get("status", "") or "")
            if not is_valid_status_transition(old_status, target_status):
                target_status = "pending_review"
            row["status"] = target_status
            rejected += 1
            synced += 1
        elif decision == "relabel":
            new_cat = str(item.get("new_category", "") or item.get("relabel_category", "")).strip()
            if new_cat:
                row["category"] = new_cat
                relabeled += 1
                synced += 1
            else:
                invalid_decision += 1
                invalid_decisions["relabel_missing_category"] += 1
        else:
            invalid_decision += 1
            invalid_decisions[decision or "unknown"] += 1

    _write_jsonl(records_file, list(mem_by_id.values()))
    _write_jsonl(queue_file, pending)
    index_synced = _sync_index_categories(records_file.with_name("auto_memory_index.json"), mem_by_id)

    report: dict[str, Any] = {
        "at": _utc_now(),
        "status": "success",
        "queue_file": str(queue_file),
        "archive_file": str(archive_file),
        "records_file": str(records_file),
        "summary": {
            "total": len(rows),
            "pending": len(pending),
            "orphan": orphan,
            "synced": synced,
            "accepted": accepted,
            "rejected": rejected,
            "relabeled": relabeled,
            "invalid_decision": invalid_decision,
            "index_category_synced": index_synced,
            "pending_oldest_hours": round(oldest_pending_hours, 3),
            "category_distribution": dict(cat_counter),
            "risk_distribution": dict(risk_counter),
            "orphan_ids_sample": orphan_ids[:20],
            "invalid_decisions": dict(invalid_decisions),
        },
    }
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
