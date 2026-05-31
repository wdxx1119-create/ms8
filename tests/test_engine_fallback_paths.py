from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ms8.engine import MemoryCoreEngine


def _engine(tmp_path: Path) -> MemoryCoreEngine:
    eng = MemoryCoreEngine(tmp_path / "ms8_home")
    eng.available = False
    eng._core = None
    eng._records_file.parent.mkdir(parents=True, exist_ok=True)
    eng._governance_log.parent.mkdir(parents=True, exist_ok=True)
    return eng


def test_write_memory_fallback_and_governance_log(tmp_path: Path) -> None:
    eng = _engine(tmp_path)
    out = eng.write_memory("remember alpha", source="ask")
    assert out["write_result"]["fallback_used"] is True
    assert out["write_result"]["reason"] == "core_unavailable"
    assert eng._records_file.exists()
    log_lines = eng._governance_log.read_text(encoding="utf-8").splitlines()
    assert log_lines
    payload = json.loads(log_lines[-1])
    assert payload["kind"] == "write"
    assert payload["error_code"] == "E_CORE_UNAVAILABLE"


def test_read_memories_skips_invalid_json_and_can_recall_false(tmp_path: Path) -> None:
    eng = _engine(tmp_path)
    rows = [
        '{"id":"ok1","text":"alpha","can_recall":true}',
        '{"id":"skip1","text":"beta","can_recall":false}',
        '{"bad-json"',
        '{"id":"ok2","normalized_text":"gamma"}',
    ]
    eng._records_file.write_text("\n".join(rows) + "\n", encoding="utf-8")
    out = eng.read_memories()
    ids = [str(r.get("id")) for r in out]
    assert ids == ["ok1", "ok2"]
    assert out[1]["text"] == "gamma"


def test_policy_recall_blocks_sensitive_and_allows_debug_query(tmp_path: Path) -> None:
    eng = _engine(tmp_path)
    secret_row = {
        "id": "s1",
        "status": "accepted",
        "scope": "personal",
        "sensitivity": "secret",
        "can_recall": True,
    }
    assert eng._policy_allows_recall(secret_row, query="alpha") is False

    debug_row = {
        "id": "d1",
        "status": "accepted",
        "scope": "system_debug",
        "sensitivity": "private",
        "can_recall": True,
    }
    assert eng._policy_allows_recall(debug_row, query="project roadmap") is False
    assert eng._policy_allows_recall(debug_row, query="ms8 debug maintenance") is True


def test_policy_blocks_expired_records(tmp_path: Path) -> None:
    eng = _engine(tmp_path)
    expired = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    row = {
        "id": "e1",
        "status": "accepted",
        "scope": "personal",
        "sensitivity": "private",
        "can_recall": True,
        "valid_until": expired,
    }
    assert eng._is_expired(row) is True
    assert eng._policy_allows_recall(row, query="anything") is False


def test_context_fallback_shape_and_inject_filter(tmp_path: Path) -> None:
    eng = _engine(tmp_path)
    rows = [
        {
            "id": "i1",
            "text": "deployment policy",
            "status": "accepted",
            "scope": "personal",
            "sensitivity": "private",
            "can_recall": True,
            "can_inject": True,
        },
        {
            "id": "i2",
            "text": "deployment policy",
            "status": "accepted",
            "scope": "personal",
            "sensitivity": "private",
            "can_recall": True,
            "can_inject": False,
        },
    ]
    eng._records_file.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )
    payload = eng.get_response_memory_context("deployment", top_k=5)
    assert payload["expression_mode"]["mode"] == "normal"
    ranked = payload.get("ranked", [])
    ids = {str(x.get("id")) for x in ranked if isinstance(x, dict)}
    assert ids == {"i1"}

