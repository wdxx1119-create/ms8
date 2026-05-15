from __future__ import annotations

import json
from pathlib import Path

from ms8.app.config import AutoMemoryConfig
from ms8.app.pipeline.memory_pipeline import MemoryPipeline


def test_pipeline_e2e_write_and_index(tmp_path: Path) -> None:
    pipeline = MemoryPipeline(tmp_path, AutoMemoryConfig(use_llm=False))
    text = "我们决定采用方案B，并更新配置参数阈值。"
    out = pipeline.process(text, source="interaction")

    assert out.status == "success"
    assert out.records
    assert out.records[0].category in {"decision", "configuration"}

    records_file = tmp_path / "memory" / "auto_memory_records.jsonl"
    assert records_file.exists()
    rows = [json.loads(x) for x in records_file.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(rows) == 1
    assert rows[0]["normalized_text"]
    assert rows[0]["status"] in {"accepted", "pending_review"}

    index_file = tmp_path / "memory" / "auto_memory_index.json"
    assert index_file.exists()
    index_payload = json.loads(index_file.read_text(encoding="utf-8"))
    if isinstance(index_payload, dict):
        assert len(index_payload.get("items", [])) >= 1
    else:
        assert len(index_payload) >= 1


def test_pipeline_e2e_duplicate_guard(tmp_path: Path) -> None:
    pipeline = MemoryPipeline(tmp_path, AutoMemoryConfig(use_llm=False))
    text = "今天测试通过，回归覆盖率提升。"

    first = pipeline.process(text, source="interaction")
    assert first.status == "success"

    # Repeated writes should eventually trigger dedupe drop within short window.
    second = pipeline.process(text, source="interaction")
    third = pipeline.process(text, source="interaction")
    fourth = pipeline.process(text, source="interaction")
    dropped = [x for x in (second, third, fourth) if x.status == "dropped"]
    assert dropped, "expected at least one dedupe drop for repeated content"


def test_pipeline_pending_review_enqueues_review_item(tmp_path: Path) -> None:
    pipeline = MemoryPipeline(tmp_path, AutoMemoryConfig(use_llm=False))
    out = pipeline.process("password=supersecret", source="interaction")
    assert out.status == "success"
    assert out.records
    assert out.records[0].status == "pending_review"
    assert out.records[0].needs_review is True

    review_file = tmp_path / "memory" / "auto_memory_review_queue.jsonl"
    assert review_file.exists()
    rows = [json.loads(x) for x in review_file.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert rows
    assert rows[-1]["decision"] == "pending"
    assert rows[-1]["memory_id"]
    assert rows[-1]["risk_level"] in {"high", "medium", "low", "critical"}


def test_pipeline_rejected_route_does_not_enqueue_review(tmp_path: Path) -> None:
    pipeline = MemoryPipeline(tmp_path, AutoMemoryConfig(use_llm=False))
    out = pipeline.process("ok", source="interaction")
    assert out.status == "dropped"

    review_file = tmp_path / "memory" / "auto_memory_review_queue.jsonl"
    if review_file.exists():
        rows = [x for x in review_file.read_text(encoding="utf-8").splitlines() if x.strip()]
        assert rows == []
