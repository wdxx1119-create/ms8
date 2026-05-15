from __future__ import annotations

import json
from pathlib import Path

from ms8.engine import MemoryCoreEngine


class _CoreNoop:
    def append_interaction(self, _text: str) -> None:
        return None


def _read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        rows.append(json.loads(ln))
    return rows


def test_fallback_record_has_governance_fields(tmp_path) -> None:
    eng = MemoryCoreEngine(tmp_path)
    eng.available = False
    eng._core = None
    out = eng.write_memory("same memory", source="ask")
    assert out["write_result"]["fallback_used"] is True
    rows = _read_rows(tmp_path / "memory" / "auto_memory_records.jsonl")
    assert len(rows) == 1
    row = rows[0]
    assert row.get("id")
    assert row.get("normalized_text")
    assert row.get("category")
    assert row.get("status")
    assert row.get("source") == "ask"
    assert isinstance(row.get("meta"), dict)
    assert row["meta"].get("admission")


def test_core_success_does_not_double_write_when_record_exists(tmp_path) -> None:
    eng = MemoryCoreEngine(tmp_path)
    eng.available = True
    eng._core = _CoreNoop()
    eng._records_file.parent.mkdir(parents=True, exist_ok=True)
    existing = {
        "id": "r1",
        "text": "same memory",
        "normalized_text": "same memory",
        "category": "general",
        "status": "accepted",
        "source": "ask",
        "created_at": "2026-01-01T00:00:00+00:00",
        "meta": {"admission": "manual"},
    }
    eng._records_file.write_text(json.dumps(existing, ensure_ascii=False) + "\n", encoding="utf-8")
    out = eng.write_memory("same memory", source="ask")
    rows = _read_rows(eng._records_file)
    assert len(rows) == 1
    assert out["write_result"]["fallback_used"] is False
    assert out["write_result"]["reason"] == "core_already_persisted"


def test_fallback_log_contains_error_code(tmp_path) -> None:
    eng = MemoryCoreEngine(tmp_path)
    eng._log_fallback("write", "core_unavailable", {"source": "ask"})
    log_file = tmp_path / "health" / "governance_fallback_log.jsonl"
    rows = _read_rows(log_file)
    assert rows
    assert rows[-1]["reason"] == "core_unavailable"
    assert rows[-1]["error_code"] == "E_CORE_UNAVAILABLE"
