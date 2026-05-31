from __future__ import annotations

import json
from pathlib import Path

from ms8.engine import MemoryCoreEngine


def _mk_engine(tmp_path: Path) -> MemoryCoreEngine:
    eng = MemoryCoreEngine(tmp_path / "ms8_home")
    eng.available = False
    eng._core = None
    eng._records_file.parent.mkdir(parents=True, exist_ok=True)
    eng._governance_log.parent.mkdir(parents=True, exist_ok=True)
    return eng


def test_policy_candidate_and_product_decision_filters(tmp_path: Path) -> None:
    eng = _mk_engine(tmp_path)
    candidate = {
        "status": "candidate",
        "can_recall": True,
        "scope": "personal",
        "sensitivity": "private",
    }
    assert eng._policy_allows_recall(candidate, query="anything") is False

    product_decision = {
        "status": "accepted",
        "category": "product_decision",
        "can_recall": True,
        "scope": "project",
        "sensitivity": "private",
    }
    assert eng._policy_allows_recall(product_decision, query="just chat") is False
    assert eng._policy_allows_recall(product_decision, query="需要做方案取舍和优先级") is True


def test_filter_rows_by_policy_inject_mode(tmp_path: Path) -> None:
    eng = _mk_engine(tmp_path)
    rows = [
        {
            "id": "a",
            "status": "accepted",
            "can_recall": True,
            "can_inject": True,
            "scope": "project",
            "sensitivity": "private",
        },
        {
            "id": "b",
            "status": "accepted",
            "can_recall": True,
            "can_inject": False,
            "scope": "project",
            "sensitivity": "private",
        },
    ]
    allowed, trace = eng._filter_rows_by_policy(rows, query="x", purpose="inject", limit=10)
    assert [r["id"] for r in allowed] == ["a"]
    assert trace["purpose"] == "inject"
    assert trace["blocked_total"] == 1


def test_write_memory_core_write_disabled_env(monkeypatch, tmp_path: Path) -> None:
    eng = _mk_engine(tmp_path)
    eng.available = True
    eng._core = object()
    monkeypatch.setenv("MS8_USE_CORE_WRITE", "0")
    out = eng.write_memory("alpha", source="ask")
    assert out["write_result"]["fallback_used"] is True
    assert out["write_result"]["reason"] == "core_write_disabled"


def test_search_memories_retrieval_disabled(monkeypatch, tmp_path: Path) -> None:
    eng = _mk_engine(tmp_path)
    eng.available = True
    eng._core = object()
    rows = [
        {"id": "1", "text": "hello world", "status": "accepted", "scope": "project", "sensitivity": "private"},
        {"id": "2", "text": "other", "status": "accepted", "scope": "project", "sensitivity": "private"},
    ]
    eng._records_file.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in rows) + "\n", encoding="utf-8")
    monkeypatch.setenv("MS8_USE_CORE_RETRIEVAL", "off")
    out = eng.search_memories("hello", limit=5)
    assert [x["id"] for x in out] == ["1"]


def test_search_memories_core_returns_empty_then_fallback(monkeypatch, tmp_path: Path) -> None:
    class _Core:
        def retrieve_memories(self, **_kwargs):  # noqa: ANN003
            return []

    eng = _mk_engine(tmp_path)
    eng.available = True
    eng._core = _Core()
    rows = [
        {"id": "1", "text": "policy route", "status": "accepted", "scope": "project", "sensitivity": "private"},
    ]
    eng._records_file.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in rows) + "\n", encoding="utf-8")
    monkeypatch.setenv("MS8_USE_CORE_RETRIEVAL", "1")
    out = eng.search_memories("policy", limit=5)
    assert out and out[0]["id"] == "1"


def test_count_last_write_and_status_proxies(tmp_path: Path) -> None:
    class _Core:
        def run_self_check(self, level: str = "L4") -> dict:
            return {"status": "ok", "level": level}

        def get_monitoring_status(self) -> dict:
            return {"enabled": True}

        def shadow_status(self) -> dict:
            return {"status": "ok"}

        def get_knowledge_graph_stats(self) -> dict:
            return {"entity_total": 3, "relation_total": 2}

        def run_maintenance_now(self, force: bool = True) -> dict:
            return {"status": "ok", "force": force}

    eng = _mk_engine(tmp_path)
    eng.available = True
    eng._core = _Core()
    rows = [
        {
            "id": "1",
            "text": "a",
            "created_at": "2026-01-01T00:00:00Z",
            "status": "accepted",
            "scope": "project",
            "sensitivity": "private",
        }
    ]
    eng._records_file.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in rows) + "\n", encoding="utf-8")
    assert eng.count_memories() == 1
    assert eng.last_write_time() == "2026-01-01T00:00:00Z"
    assert eng.run_self_check("L4")["status"] == "ok"
    assert eng.get_monitoring_status()["enabled"] is True
    assert eng.shadow_status()["status"] == "ok"
    assert eng.get_knowledge_graph_stats()["entity_total"] == 3
    assert eng.run_maintenance_now(force=False)["force"] is False
