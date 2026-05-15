from __future__ import annotations

import json
from pathlib import Path

from ms8.engine import MemoryCoreEngine


def test_write_search_context_integration(tmp_path: Path) -> None:
    runtime = tmp_path / "ms8_runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    engine = MemoryCoreEngine(runtime)

    saved = engine.write_memory("集成测试：偏好 Python 工程实践", source="test:e2e")
    assert "id" in saved
    assert str(saved.get("source", "")).startswith("test:e2e")

    hits = engine.search_memories("Python", limit=10)
    assert isinstance(hits, list)
    assert len(hits) >= 1

    ctx = engine.get_response_memory_context("给我一个Python工程实践建议", top_k=5)
    assert isinstance(ctx, dict)
    assert "expression_mode" in ctx
    expr = ctx.get("expression_mode", {})
    assert isinstance(expr, dict)
    assert str(expr.get("mode", "normal")) in {"normal", "light", "strong"}


def test_governance_fields_and_recall_filter_integration(tmp_path: Path) -> None:
    runtime = tmp_path / "ms8_runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    engine = MemoryCoreEngine(runtime)

    saved = engine.write_memory("治理链路测试：必须保留状态字段", source="test:governance")
    rec_id = str(saved.get("id", ""))
    rows = engine.read_memories()
    row = next((r for r in rows if str(r.get("id", "")) == rec_id), None)
    assert isinstance(row, dict)
    assert str(row.get("status", "")) in {
        "candidate",
        "short_term",
        "accepted",
        "verified",
        "pending_review",
        "quarantined",
        "stale",
        "superseded",
        "revoked",
    }
    assert "scope" in row
    assert "authority" in row
    assert "sensitivity" in row
    assert "can_recall" in row
    assert "can_inject" in row

    # Inject a hidden record and verify search path doesn't surface it.
    hidden = {
        "id": "hidden-1",
        "text": "隐藏记录 should not be recalled",
        "normalized_text": "隐藏记录 should not be recalled",
        "category": "general",
        "status": "accepted",
        "source": "test:hidden",
        "scope": "personal",
        "authority": "user_explicit",
        "sensitivity": "private",
        "can_recall": False,
        "can_inject": False,
        "can_act_on": False,
        "meta": {"admission": "ms8_write_guard_v1"},
    }
    records_file = runtime / "memory" / "auto_memory_records.jsonl"
    with records_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(hidden, ensure_ascii=False) + "\n")

    hits = engine.search_memories("隐藏记录", limit=20)
    assert all(str(h.get("id", "")) != "hidden-1" for h in hits)


def test_state_machine_visibility_rules_integration(tmp_path: Path) -> None:
    runtime = tmp_path / "ms8_runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    engine = MemoryCoreEngine(runtime)

    records_file = runtime / "memory" / "auto_memory_records.jsonl"
    rows = [
        {
            "id": "st-pending",
            "text": "pending record should not be recalled",
            "normalized_text": "pending record should not be recalled",
            "category": "general",
            "status": "pending_review",
            "source": "test:state",
            "scope": "personal",
            "authority": "user_explicit",
            "sensitivity": "private",
            "can_recall": True,
            "can_inject": False,
            "can_act_on": False,
            "meta": {"admission": "ms8_write_guard_v1"},
        },
        {
            "id": "st-quarantine",
            "text": "quarantined record should not be recalled",
            "normalized_text": "quarantined record should not be recalled",
            "category": "general",
            "status": "quarantined",
            "source": "test:state",
            "scope": "personal",
            "authority": "user_explicit",
            "sensitivity": "private",
            "can_recall": True,
            "can_inject": False,
            "can_act_on": False,
            "meta": {"admission": "ms8_write_guard_v1"},
        },
        {
            "id": "st-superseded",
            "text": "superseded record should not be recalled",
            "normalized_text": "superseded record should not be recalled",
            "category": "general",
            "status": "superseded",
            "superseded_by": "st-accepted",
            "source": "test:state",
            "scope": "personal",
            "authority": "user_explicit",
            "sensitivity": "private",
            "can_recall": True,
            "can_inject": False,
            "can_act_on": False,
            "meta": {"admission": "ms8_write_guard_v1"},
        },
        {
            "id": "st-accepted",
            "text": "accepted record should be recalled",
            "normalized_text": "accepted record should be recalled",
            "category": "general",
            "status": "accepted",
            "source": "test:state",
            "scope": "personal",
            "authority": "user_explicit",
            "sensitivity": "private",
            "can_recall": True,
            "can_inject": True,
            "can_act_on": False,
            "meta": {"admission": "ms8_write_guard_v1"},
        },
    ]
    with records_file.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    hits = engine.search_memories("record should", limit=50)
    ids = {str(h.get("id", "")) for h in hits}
    assert "st-accepted" in ids
    assert "st-pending" not in ids
    assert "st-quarantine" not in ids
    assert "st-superseded" not in ids
