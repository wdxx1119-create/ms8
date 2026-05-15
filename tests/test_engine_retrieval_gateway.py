from __future__ import annotations

import json
from pathlib import Path

from ms8.engine import MemoryCoreEngine


def _build_engine(tmp_path: Path) -> MemoryCoreEngine:
    root = tmp_path / "ms8_home"
    eng = MemoryCoreEngine(root)
    # Force fallback lane to avoid depending on MemoryCore internals in this unit test.
    eng.available = False
    eng._core = None
    eng._records_file.parent.mkdir(parents=True, exist_ok=True)
    return eng


def test_search_memories_uses_recall_policy(tmp_path: Path) -> None:
    eng = _build_engine(tmp_path)
    rows = [
        {
            "id": "a1",
            "text": "project roadmap",
            "status": "accepted",
            "scope": "personal",
            "sensitivity": "private",
            "can_recall": True,
            "can_inject": True,
        },
        {
            "id": "a2",
            "text": "project roadmap",
            "status": "accepted",
            "scope": "personal",
            "sensitivity": "private",
            "can_recall": False,
            "can_inject": True,
        },
    ]
    eng._records_file.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )
    out = eng.search_memories("roadmap", limit=10)
    ids = {str(x.get("id")) for x in out}
    assert "a1" in ids
    assert "a2" not in ids


def test_context_injection_requires_can_inject(tmp_path: Path) -> None:
    eng = _build_engine(tmp_path)
    rows = [
        {
            "id": "b1",
            "text": "deployment policy",
            "status": "accepted",
            "scope": "personal",
            "sensitivity": "private",
            "can_recall": True,
            "can_inject": False,
        },
        {
            "id": "b2",
            "text": "deployment policy",
            "status": "accepted",
            "scope": "personal",
            "sensitivity": "private",
            "can_recall": True,
            "can_inject": True,
        },
    ]
    eng._records_file.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )
    payload = eng.get_response_memory_context("deployment", top_k=5)
    ranked = payload.get("ranked", [])
    ids = {str(x.get("id")) for x in ranked if isinstance(x, dict)}
    assert "b2" in ids
    assert "b1" not in ids
    trace = payload.get("retrieval_gateway", {})
    assert trace.get("purpose") == "inject"
