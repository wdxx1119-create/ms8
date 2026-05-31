from __future__ import annotations

import json
from pathlib import Path

import ms8.record_policy as rp


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def test_validate_file_and_quarantine_filters_invalid_rows(tmp_path: Path) -> None:
    records = tmp_path / "records.jsonl"
    quarantine = tmp_path / "quarantine.jsonl"
    valid = rp.build_canonical_record("hello", "ask")
    lines = [
        "{bad-json",
        json.dumps(["not-dict"], ensure_ascii=False),
        json.dumps({"id": "x", "text": "t"}, ensure_ascii=False),
        json.dumps(valid, ensure_ascii=False),
    ]
    records.write_text("\n".join(lines) + "\n", encoding="utf-8")

    rp.validate_file_and_quarantine(records, quarantine)

    kept = _read_jsonl(records)
    assert len(kept) == 1
    assert kept[0]["id"] == valid["id"]

    q = _read_jsonl(quarantine)
    reasons = {row["reason"] for row in q}
    assert "invalid_json" in reasons
    assert "invalid_type" in reasons
    assert any(r.startswith("missing:") for r in reasons)


def test_append_canonical_record_fallback_path(monkeypatch, tmp_path: Path) -> None:
    records = tmp_path / "records.jsonl"
    quarantine = tmp_path / "quarantine.jsonl"
    calls = {"n": 0}
    original = rp.validate_record

    def _fake_validate(row: dict):
        calls["n"] += 1
        if calls["n"] == 1:
            return False, "forced_invalid"
        return original(row)

    monkeypatch.setattr(rp, "validate_record", _fake_validate)
    row, ok, reason = rp.append_canonical_record(
        records_file=records,
        quarantine_file=quarantine,
        text="abc",
        source="ask",
        status="accepted",
    )
    assert calls["n"] >= 2
    assert row["meta"]["admission"] == "ms8_write_guard_v1_fallback"
    assert ok is True
    assert reason == "ok"
    q = _read_jsonl(quarantine)
    assert q and q[0]["reason"] == "forced_invalid"


def test_repair_scope_flags_dry_run_does_not_write(tmp_path: Path) -> None:
    records = tmp_path / "records.jsonl"
    rows = [
        {"id": "1", "text": "self-check failed", "normalized_text": "self-check failed", "status": "accepted", "source": "system", "category": "general"},
        {"id": "2", "text": "我喜欢简洁输出", "normalized_text": "我喜欢简洁输出", "status": "accepted", "source": "ask", "category": "general"},
    ]
    records.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    before = records.read_text(encoding="utf-8")

    out = rp.repair_scope_flags(records, dry_run=True)
    after = records.read_text(encoding="utf-8")

    assert out["mode"] == "dry_run"
    assert out["updated"] >= 1
    assert before == after
    assert out["field_completeness"]["scope"] >= 0.0


def test_infer_scope_flags_labs_and_decision_paths() -> None:
    labs = rp.infer_scope_flags("实验观察", "labs.synthetic")
    assert labs["scope"] == "labs"
    assert labs["can_inject"] is False

    decision = rp.infer_scope_flags("这个发布方案和优先级要定下来", "system")
    assert decision["scope"] in {"project", "system_debug"}
