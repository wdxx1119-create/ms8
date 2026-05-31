from __future__ import annotations

import json
from pathlib import Path

import ms8.record_policy as rp


def test_validate_record_rejects_invalid_shapes() -> None:
    row = rp.build_canonical_record("hello", "ask")
    ok, reason = rp.validate_record({**row, "meta": "bad"})
    assert ok is False and reason == "invalid:meta_type"

    bad_bool = dict(row)
    bad_bool["can_inject"] = "true"
    ok, reason = rp.validate_record(bad_bool)
    assert ok is False and reason == "invalid:can_inject_type"

    bad_status = dict(row)
    bad_status["status"] = "unknown"
    ok, reason = rp.validate_record(bad_status)
    assert ok is False and reason == "invalid:status"


def test_validate_file_and_transition_edges(tmp_path: Path) -> None:
    records = tmp_path / "records.jsonl"
    quarantine = tmp_path / "quarantine.jsonl"
    records.write_text(
        "\n".join(
            [
                json.dumps(rp.build_canonical_record("ok", "ask"), ensure_ascii=False),
                json.dumps({"id": "x", "normalized_text": "y", "status": "accepted", "source": "ask", "meta": {"admission": "x"}}, ensure_ascii=False),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    rp.validate_file_and_quarantine(records, quarantine)
    kept = [x for x in records.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(kept) == 1
    q_rows = [json.loads(x) for x in quarantine.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert any(str(r.get("reason", "")).startswith("missing:") for r in q_rows)

    assert rp.is_valid_status_transition(None, "accepted") is True
    assert rp.is_valid_status_transition("accepted", "accepted") is True
    assert rp.is_valid_status_transition("revoked", "verified") is False


def test_repair_scope_flags_applies_decision_and_preference_categories(tmp_path: Path) -> None:
    records = tmp_path / "records.jsonl"
    rows = [
        {
            "id": "d1",
            "text": "这个发布方案和优先级今天决定",
            "normalized_text": "这个发布方案和优先级今天决定",
            "category": "general",
            "status": "accepted",
            "source": "ask",
            "meta": {"admission": "x"},
        },
        {
            "id": "p1",
            "text": "我喜欢简洁输出",
            "normalized_text": "我喜欢简洁输出",
            "category": "general",
            "status": "accepted",
            "source": "ask",
            "meta": {"admission": "x"},
        },
    ]
    records.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    out = rp.repair_scope_flags(records, dry_run=False)
    assert out["updated"] >= 2
    parsed = [json.loads(x) for x in records.read_text(encoding="utf-8").splitlines() if x.strip()]
    by_id = {r["id"]: r for r in parsed}
    assert by_id["d1"]["scope"] == "project"
    assert by_id["d1"]["category"] == "product_decision"
    assert by_id["p1"]["category"] == "user_preference"
