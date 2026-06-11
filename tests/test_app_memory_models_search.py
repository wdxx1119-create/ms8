from __future__ import annotations

from ms8.app.memory.models import record_to_dict
from ms8.app.memory.search import MemorySearch
from ms8.app.schemas.pipeline_schema import MemoryRecord


class _IndexerStub:
    def __init__(self, rows):
        self.rows = rows

    def search(self, text: str, limit: int = 10):
        return self.rows[:limit]


def test_record_to_dict_roundtrip():
    rec = MemoryRecord(
        text="hello",
        normalized_text="hello",
        category="preference",
        confidence=0.88,
        meta={"id": "abc-1"},
    )
    out = record_to_dict(rec)
    assert out["text"] == "hello"
    assert out["meta"]["id"] == "abc-1"


def test_memory_search_query_and_unified_warns():
    row = {
        "text": "hello world",
        "normalized_text": "hello world",
        "confidence": 0.7,
        "source": "unit",
        "category": "test",
        "created_at": "2026-05-18T00:00:00+00:00",
        "meta": {"id": "r1"},
        "entities": ["ms8"],
    }
    s = MemorySearch(_IndexerStub([row]))

    plain = s.query("hello", limit=5)
    assert len(plain) == 1

    import warnings

    with warnings.catch_warnings(record=True) as ws:
        warnings.simplefilter("always")
        unified = s.query_unified("hello", limit=5)
    assert ws
    assert "deprecated" in str(ws[0].message).lower()
    assert unified[0]["id"] == "r1"
    assert unified[0]["scores"]["fusion"] == 0.7
    assert unified[0]["signals"]["matched_entities"] == ["ms8"]


def test_memory_search_query_unified_fallback_id():
    row = {"normalized_text": "x", "confidence": 0.3}
    s = MemorySearch(_IndexerStub([row]))
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        out = s.query_unified("x")
    assert out[0]["id"].startswith("idx-")
