from __future__ import annotations

import json
from pathlib import Path

from ms8 import review_governance as rg


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + ("\n" if rows else ""), encoding="utf-8")


def test_parse_dt_and_read_jsonl_invalid_lines(tmp_path: Path) -> None:
    p = tmp_path / "x.jsonl"
    p.write_text('\n  \n{"a":1}\nnot-json\n[1,2,3]\n{"b":2}\n', encoding="utf-8")
    rows = rg._read_jsonl(p)
    assert rows == [{"a": 1}, {"b": 2}]
    assert rg._parse_dt("2026-01-01T00:00:00Z") is not None
    assert rg._parse_dt("2026-01-01T00:00:00") is not None
    assert rg._parse_dt("bad") is None


def test_sync_index_categories_list_and_dict(tmp_path: Path) -> None:
    mem = {
        "m1": {"id": "m1", "category": "new1"},
        "m2": {"id": "m2", "category": "new2"},
    }

    # list payload path
    idx1 = tmp_path / "idx1.json"
    idx1.write_text(json.dumps([{"id": "m1", "category": "old"}]), encoding="utf-8")
    touched1 = rg._sync_index_categories(idx1, mem)
    data1 = json.loads(idx1.read_text(encoding="utf-8"))
    assert touched1 == 1
    assert data1[0]["category"] == "new1"

    # dict payload path
    idx2 = tmp_path / "idx2.json"
    idx2.write_text(
        json.dumps(
            {
                "items": [{"id": "m1", "category": "old"}],
                "hot_items": [{"meta": {"id": "m2"}, "category": "old"}],
                "cold_items": [{"id": "unknown", "category": "x"}],
            }
        ),
        encoding="utf-8",
    )
    touched2 = rg._sync_index_categories(idx2, mem)
    data2 = json.loads(idx2.read_text(encoding="utf-8"))
    assert touched2 == 2
    assert data2["items"][0]["category"] == "new1"
    assert data2["hot_items"][0]["category"] == "new2"


def test_archive_and_sync_review_queue_reject_normal_and_index_missing(tmp_path: Path) -> None:
    queue = tmp_path / "memory" / "auto_memory_review_queue.jsonl"
    records = tmp_path / "memory" / "auto_memory_records.jsonl"
    report_file = tmp_path / "health" / "review_governance_latest.json"

    _write_jsonl(
        records,
        [
            {
                "id": "m1",
                "text": "abc",
                "normalized_text": "abc",
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
                "decision": "rejected",
                "confidence": 0.2,
                "category": "general",
                "risk_level": "normal",
            }
        ],
    )

    out = rg.archive_and_sync_review_queue(
        queue_file=queue,
        records_file=records,
        archive_dir=tmp_path / "memory",
        report_file=report_file,
    )
    assert out["status"] == "success"
    assert out["summary"]["rejected"] == 1
    assert out["summary"]["index_category_synced"] == 0

    rows = [json.loads(x) for x in records.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert rows[0]["status"] == "revoked"

