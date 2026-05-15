from __future__ import annotations

import json
from pathlib import Path

from ms8.review_governance import archive_and_sync_review_queue


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + ("\n" if rows else ""),
        encoding="utf-8",
    )


def test_archive_and_sync_review_queue(tmp_path: Path) -> None:
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
            },
            {
                "id": "m2",
                "text": "b",
                "normalized_text": "b",
                "category": "general",
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
                "confidence": 0.91,
                "category": "general",
                "risk_level": "normal",
            },
            {
                "memory_id": "m2",
                "decision": "rejected",
                "confidence": 0.2,
                "category": "general",
                "risk_level": "high",
            },
            {
                "memory_id": "missing",
                "decision": "accepted",
                "confidence": 0.8,
                "category": "general",
                "risk_level": "normal",
            },
            {
                "memory_id": "m1",
                "decision": "pending",
                "confidence": 0.3,
                "category": "general",
                "risk_level": "normal",
            },
            {
                "memory_id": "m1",
                "decision": "mystery_decision",
                "confidence": 0.5,
                "category": "general",
                "risk_level": "normal",
            },
        ],
    )

    out = archive_and_sync_review_queue(
        queue_file=queue,
        records_file=records,
        archive_dir=archive_dir,
        report_file=report_file,
    )
    assert out["status"] == "success"
    assert out["summary"]["orphan"] == 1
    assert out["summary"]["pending"] == 1
    assert out["summary"]["invalid_decision"] == 1
    assert "mystery_decision" in out["summary"]["invalid_decisions"]
    assert "missing" in out["summary"]["orphan_ids_sample"]
    assert Path(out["archive_file"]).exists()
    assert report_file.exists()

    updated = [json.loads(x) for x in records.read_text(encoding="utf-8").splitlines() if x.strip()]
    m1 = next(x for x in updated if x["id"] == "m1")
    m2 = next(x for x in updated if x["id"] == "m2")
    assert m1["status"] == "verified"
    assert m2["status"] == "quarantined"
