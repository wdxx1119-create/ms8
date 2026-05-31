from __future__ import annotations

import json
from pathlib import Path

from ms8.app.config import AutoMemoryConfig
from ms8.app.pipeline.memory_pipeline import MemoryPipeline
from ms8.review_governance import archive_and_sync_review_queue


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def test_review_decision_writeback_updates_record_status(tmp_path: Path) -> None:
    pipeline = MemoryPipeline(tmp_path, AutoMemoryConfig(use_llm=False))
    out = pipeline.process("password=supersecret", source="interaction")
    assert out.status == "success"

    queue_file = tmp_path / "memory" / "auto_memory_review_queue.jsonl"
    records_file = tmp_path / "memory" / "auto_memory_records.jsonl"
    archive_dir = tmp_path / "memory" / "archive"
    report_file = tmp_path / "health" / "review_governance_latest.json"

    queue_rows = _read_jsonl(queue_file)
    assert queue_rows
    record_rows = _read_jsonl(records_file)
    rec_row = record_rows[-1] if record_rows else {}
    rec_id = str(rec_row.get("id", "") or rec_row.get("meta", {}).get("id", ""))
    if not rec_id:
        # Some flows may queue review first and persist main record later.
        for item in queue_rows:
            item_id = str(item.get("memory_id", "") or item.get("record_id", "") or item.get("id", ""))
            if item_id:
                rec_id = item_id
                break
    assert rec_id
    if not record_rows:
        records_file.parent.mkdir(parents=True, exist_ok=True)
        seed_record = {
            "id": rec_id,
            "content": "password=supersecret",
            "category": out.records[0].category,
            "status": "accepted",
            "source": "interaction",
        }
        records_file.write_text(json.dumps(seed_record, ensure_ascii=False) + "\n", encoding="utf-8")
    target = None
    for item in queue_rows:
        item_id = str(item.get("memory_id", "") or item.get("record_id", "") or item.get("id", ""))
        if item_id == rec_id:
            target = item
            break
    if target is None:
        target = {
            "memory_id": rec_id,
            "category": out.records[0].category,
            "confidence": 0.8,
        }
        queue_rows.append(target)
    target["decision"] = "rejected"
    target["risk_level"] = "high"
    queue_file.write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in queue_rows) + "\n",
        encoding="utf-8",
    )

    report = archive_and_sync_review_queue(
        queue_file=queue_file,
        records_file=records_file,
        archive_dir=archive_dir,
        report_file=report_file,
    )
    assert report["status"] == "success"
    assert report["summary"]["rejected"] >= 1

    updated_rows = _read_jsonl(records_file)
    row = next(
        x
        for x in updated_rows
        if str(x.get("id", "") or x.get("meta", {}).get("id", "")) == rec_id
    )
    assert row["status"] == "quarantined"


def test_review_relabel_writeback_updates_category(tmp_path: Path) -> None:
    pipeline = MemoryPipeline(tmp_path, AutoMemoryConfig(use_llm=False))
    out = pipeline.process("我们决定采用方案B并更新模块实现", source="interaction")
    assert out.status == "success"
    rec_id = str(out.records[0].meta.get("id", ""))
    assert rec_id

    queue_file = tmp_path / "memory" / "auto_memory_review_queue.jsonl"
    records_file = tmp_path / "memory" / "auto_memory_records.jsonl"
    archive_dir = tmp_path / "memory" / "archive"
    report_file = tmp_path / "health" / "review_governance_latest.json"

    # For accepted/indexed records, synthesize a review decision item to test writeback sync.
    queue_rows = _read_jsonl(queue_file)
    queue_rows.append(
        {
            "memory_id": rec_id,
            "decision": "relabel",
            "new_category": "technical_doc",
            "category": out.records[0].category,
            "risk_level": "low",
            "confidence": 0.8,
        }
    )
    queue_file.write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in queue_rows) + "\n",
        encoding="utf-8",
    )

    report = archive_and_sync_review_queue(
        queue_file=queue_file,
        records_file=records_file,
        archive_dir=archive_dir,
        report_file=report_file,
    )
    assert report["status"] == "success"
    assert report["summary"]["relabeled"] >= 1
    assert "index_category_synced" in report["summary"]

    updated_rows = _read_jsonl(records_file)
    row = next(x for x in updated_rows if str(x.get("id", "")) == rec_id)
    assert row["category"] == "technical_doc"

    index_file = tmp_path / "memory" / "auto_memory_index.json"
    index_payload = json.loads(index_file.read_text(encoding="utf-8"))
    items = index_payload.get("items", []) if isinstance(index_payload, dict) else index_payload
    idx_row = next(x for x in items if str(x.get("id", "")) == rec_id)
    assert idx_row["category"] == "technical_doc"
