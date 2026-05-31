from __future__ import annotations

import json
from pathlib import Path

from ms8.record_policy import (
    build_canonical_record,
    infer_scope_flags,
    is_valid_status_transition,
    validate_record,
)
from ms8.review_governance import archive_and_sync_review_queue


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + ("\n" if rows else ""),
        encoding="utf-8",
    )


def test_record_policy_debug_scope_is_not_injectable() -> None:
    payload = infer_scope_flags("self-check failed in debug mode", "system")
    assert payload["scope"] == "system_debug"
    assert payload["can_inject"] is False
    assert payload["can_recall"] is True

    row = build_canonical_record("self-check failed", "system")
    ok, reason = validate_record(row)
    assert ok is True, reason

    row["can_inject"] = True
    ok, reason = validate_record(row)
    assert ok is False
    assert reason == "invalid:debug_can_inject"


def test_record_policy_status_transition_rules() -> None:
    assert is_valid_status_transition("accepted", "verified") is True
    assert is_valid_status_transition("verified", "accepted") is False
    assert is_valid_status_transition("revoked", "accepted") is False
    assert is_valid_status_transition("", "candidate") is True
    assert is_valid_status_transition("candidate", "unknown") is False


def test_review_governance_empty_queue_report(tmp_path: Path) -> None:
    queue = tmp_path / "memory" / "auto_memory_review_queue.jsonl"
    records = tmp_path / "memory" / "auto_memory_records.jsonl"
    archive_dir = tmp_path / "memory"
    report_file = tmp_path / "health" / "review_governance_latest.json"

    out = archive_and_sync_review_queue(
        queue_file=queue,
        records_file=records,
        archive_dir=archive_dir,
        report_file=report_file,
    )

    assert out["status"] == "skipped"
    assert out["reason"] == "queue_empty_or_missing"
    assert report_file.exists()


def test_review_governance_relabel_without_new_category_is_invalid(tmp_path: Path) -> None:
    queue = tmp_path / "memory" / "auto_memory_review_queue.jsonl"
    records = tmp_path / "memory" / "auto_memory_records.jsonl"
    archive_dir = tmp_path / "memory"
    report_file = tmp_path / "health" / "review_governance_latest.json"

    _write_jsonl(
        records,
        [
            {
                "id": "m1",
                "text": "a",
                "normalized_text": "a",
                "category": "general",
                "status": "accepted",
                "source": "ask",
                "meta": {"admission": "x"},
            }
        ],
    )
    _write_jsonl(
        queue,
        [
            {
                "memory_id": "m1",
                "decision": "relabel",
                "confidence": 0.8,
                "category": "general",
                "risk_level": "normal",
            }
        ],
    )

    out = archive_and_sync_review_queue(
        queue_file=queue,
        records_file=records,
        archive_dir=archive_dir,
        report_file=report_file,
    )
    assert out["status"] == "success"
    assert out["summary"]["invalid_decision"] == 1
    assert out["summary"]["invalid_decisions"]["relabel_missing_category"] == 1

