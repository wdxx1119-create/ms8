from __future__ import annotations

import json
from pathlib import Path

from ms8.engine import MemoryCoreEngine


def _write_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")


def test_recall_filter_blocks_risky_states_and_system_debug(tmp_path: Path) -> None:
    e = MemoryCoreEngine(tmp_path)
    e.available = False
    records = e.records_file()
    _write_rows(
        records,
        [
            {
                "id": "a",
                "text": "normal memory",
                "normalized_text": "normal memory",
                "category": "general",
                "status": "accepted",
                "source": "ask",
                "meta": {"admission": "x"},
                "scope": "personal",
            },
            {
                "id": "b",
                "text": "debug self-check note",
                "normalized_text": "debug self-check note",
                "category": "system_diagnostic",
                "status": "accepted",
                "source": "system",
                "meta": {"admission": "x"},
                "scope": "system_debug",
            },
            {
                "id": "c",
                "text": "pending review item",
                "normalized_text": "pending review item",
                "category": "general",
                "status": "pending_review",
                "source": "ask",
                "meta": {"admission": "x"},
            },
        ],
    )
    out_normal = e.search_memories("note", limit=10)
    assert len(out_normal) == 0
    out_debug = e.search_memories("self-check", limit=10)
    assert len(out_debug) == 1
    assert out_debug[0]["id"] == "b"


def test_recall_filter_blocks_sensitive_and_superseded(tmp_path: Path) -> None:
    e = MemoryCoreEngine(tmp_path)
    e.available = False
    records = e.records_file()
    _write_rows(
        records,
        [
            {
                "id": "s1",
                "text": "token is abc",
                "normalized_text": "token is abc",
                "category": "general",
                "status": "accepted",
                "source": "ask",
                "meta": {"admission": "x"},
                "sensitivity": "credential",
            },
            {
                "id": "s2",
                "text": "old preference",
                "normalized_text": "old preference",
                "category": "general",
                "status": "accepted",
                "source": "ask",
                "meta": {"admission": "x"},
                "superseded_by": "new-id",
            },
            {
                "id": "ok",
                "text": "safe fact",
                "normalized_text": "safe fact",
                "category": "general",
                "status": "verified",
                "source": "ask",
                "meta": {"admission": "x"},
            },
        ],
    )
    out = e.search_memories("fact", limit=10)
    assert len(out) == 1
    assert out[0]["id"] == "ok"
