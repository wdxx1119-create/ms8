from __future__ import annotations

import json
from pathlib import Path

from ms8 import review_governance as rg


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + ("\n" if rows else "")
    path.write_text(payload, encoding="utf-8")


def test_sync_index_categories_tolerates_bad_index_json(tmp_path: Path) -> None:
    idx = tmp_path / "auto_memory_index.json"
    idx.write_text("{bad", encoding="utf-8")
    changed = rg._sync_index_categories(idx, {"m1": {"id": "m1", "category": "x"}})
    assert changed == 0


def test_archive_and_sync_handles_invalid_transition_and_relabel(tmp_path: Path, monkeypatch) -> None:
    queue = tmp_path / "memory" / "auto_memory_review_queue.jsonl"
    records = tmp_path / "memory" / "auto_memory_records.jsonl"
    report_file = tmp_path / "health" / "review_governance_latest.json"

    _write_jsonl(
        records,
        [
            {
                "id": "m1",
                "text": "t1",
                "normalized_text": "t1",
                "category": "old",
                "status": "revoked",
                "source": "ask",
                "meta": {"admission": "x"},
            },
            {
                "id": "m2",
                "text": "t2",
                "normalized_text": "t2",
                "category": "old2",
                "status": "accepted",
                "source": "ask",
                "meta": {"admission": "x"},
            },
        ],
    )
    _write_jsonl(
        queue,
        [
            {
                "memory_id": "m1",
                "decision": "accepted",
                "confidence": 0.95,
                "category": "old",
                "risk_level": "normal",
                "created_at": "bad-time",
            },
            {
                "memory_id": "m2",
                "decision": "relabel",
                "new_category": "new-cat",
                "confidence": 0.8,
                "category": "old2",
                "risk_level": "normal",
            },
            {
                "memory_id": "m2",
                "decision": "pending",
                "confidence": 0.1,
                "category": "old2",
                "risk_level": "high",
                "created_at": "2026-05-10T00:00:00+00:00",
            },
        ],
    )

    # force transition validator to deny revoked->verified path
    monkeypatch.setattr(rg, "is_valid_status_transition", lambda old, new: not (old == "revoked" and new == "verified"))

    # malformed index payload branch
    bad_index = records.with_name("auto_memory_index.json")
    bad_index.write_text(json.dumps({"items": "not-a-list"}), encoding="utf-8")

    out = rg.archive_and_sync_review_queue(
        queue_file=queue,
        records_file=records,
        archive_dir=tmp_path / "memory",
        report_file=report_file,
    )

    assert out["status"] == "success"
    assert out["summary"]["pending"] == 1
    assert out["summary"]["accepted"] == 1
    assert out["summary"]["relabeled"] == 1
    assert out["summary"]["index_category_synced"] == 0
    assert out["summary"]["pending_oldest_hours"] >= 0.0

    rows = [json.loads(x) for x in records.read_text(encoding="utf-8").splitlines() if x.strip()]
    m1 = next(x for x in rows if x["id"] == "m1")
    m2 = next(x for x in rows if x["id"] == "m2")
    # denied transition should fallback to pending_review
    assert m1["status"] == "pending_review"
    assert m2["category"] == "new-cat"

    pending_rows = [json.loads(x) for x in queue.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(pending_rows) == 1
    assert pending_rows[0]["decision"] == "pending"
