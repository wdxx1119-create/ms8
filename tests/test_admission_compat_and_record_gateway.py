from __future__ import annotations

from pathlib import Path

from ms8.engine_core import admission_compat as ac
from ms8.engine_core import record_gateway as rg


def test_admission_compat_decision_and_redaction() -> None:
    d = ac.evaluate_candidate("  hello   world  ", metadata={"a": 1})
    out = d.to_dict()
    assert out["normalized_text"] == "hello world"
    assert out["reasons"] == ["engine_core_admission_compat"]
    assert out["raw"]["metadata"]["a"] == 1

    red = ac.redact_sensitive_text("api_key=abc123 token:zzz password=pp 1234567890123456")
    text = red["redacted_text"]
    assert "[REDACTED]" in text
    assert "[REDACTED_NUM]" in text


def test_admission_batch_review_surface() -> None:
    b = ac.BatchReview(review_service=object())
    out = b.run(mode="drain", limit=3, accept_conf_min=0.8, reject_conf_max=0.1)
    assert out["status"] == "success"
    assert out["mode"] == "drain"
    assert out["limit"] == 3
    assert out["applied"] == 0


def test_record_gateway_append_and_normalize(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    row = rg.append_memory_record(memory_dir=memory_dir, text="hello", source="ask")
    assert row["text"] == "hello"
    assert row["status"] == "accepted"

    stats = rg.normalize_memory_records(memory_dir)
    assert stats["records_file"].endswith("auto_memory_records.jsonl")
    assert "field_completeness" in stats
