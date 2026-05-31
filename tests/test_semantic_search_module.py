from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ms8.engine_core import semantic_search as mod


class _DummyStore:
    def read_memory_md(self) -> str:
        return "MS8 memory about routing and governance."


def _cfg(tmp_path: Path) -> dict:
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    return {"memory_dir": memory_dir, "daily_dir": memory_dir / "daily"}


def test_tokenize_and_sparse_vector(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "get_config", lambda: _cfg(tmp_path))
    monkeypatch.setattr(mod, "FileMemoryStore", _DummyStore)
    searcher = mod.SemanticMemorySearch()

    tokens = searcher._tokenize("我喜欢MS8 routing 方案")
    assert "ms8" in tokens
    assert "我" in tokens

    sparse = searcher._sparse_vector("aa aa bb")
    assert "aa" in sparse and "bb" in sparse
    assert abs(sum(v * v for v in sparse.values()) - 1.0) < 1e-6


def test_cosine_dense_guardrails(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "get_config", lambda: _cfg(tmp_path))
    monkeypatch.setattr(mod, "FileMemoryStore", _DummyStore)
    searcher = mod.SemanticMemorySearch()
    assert searcher._cosine_dense([], []) == 0.0
    assert searcher._cosine_dense([1.0], [1.0, 2.0]) == 0.0
    assert searcher._cosine_dense([1.0, 0.0], [1.0, 0.0]) == 1.0


def test_embed_or_sparse_and_retry_state(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "get_config", lambda: _cfg(tmp_path))
    monkeypatch.setattr(mod, "FileMemoryStore", _DummyStore)
    monkeypatch.setattr(mod, "atomic_write_json", lambda *a, **k: None)
    searcher = mod.SemanticMemorySearch()

    monkeypatch.setattr(searcher, "_ollama_embedding", lambda text: None)
    payload = searcher._embed_or_sparse("doc::1", "hello")
    assert payload["dense"] is None
    assert payload["retry_count"] == 1
    assert payload["last_error"] == "embedding_unavailable"
    assert searcher._should_retry_dense(payload) is False


def test_should_retry_dense_with_expired_timestamp(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "get_config", lambda: _cfg(tmp_path))
    monkeypatch.setattr(mod, "FileMemoryStore", _DummyStore)
    searcher = mod.SemanticMemorySearch()
    searcher.failure_retry_ttl_seconds = 1
    old = {
        "dense": None,
        "retry_count": 0,
        "updated_at": "2000-01-01T00:00:00",
    }
    assert searcher._should_retry_dense(old) is True
    assert searcher._should_retry_dense({"dense": [0.1], "retry_count": 9}) is False


def test_documents_load_memory_and_daily_logs(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    daily = cfg["memory_dir"] / "2026-05-21-sample.md"
    daily.write_text("daily log content", encoding="utf-8")
    monkeypatch.setattr(mod, "get_config", lambda: cfg)
    monkeypatch.setattr(mod, "FileMemoryStore", _DummyStore)
    monkeypatch.setattr(mod, "list_daily_log_files", lambda *a, **k: [daily])

    searcher = mod.SemanticMemorySearch()
    docs = searcher._documents()
    assert any(doc["id"] == "MEMORY.md" for doc in docs)
    assert any(doc["source"].startswith("daily_log:") for doc in docs)


def test_search_fallback_scoring_without_dense(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(mod, "get_config", lambda: cfg)
    monkeypatch.setattr(mod, "FileMemoryStore", _DummyStore)
    monkeypatch.setattr(mod, "list_daily_log_files", lambda *a, **k: [])
    monkeypatch.setattr(mod, "atomic_write_json", lambda *a, **k: None)

    searcher = mod.SemanticMemorySearch()
    monkeypatch.setattr(searcher, "_ollama_embedding", lambda text: None)

    results = searcher.search("MS8", top_k=3)
    assert results
    assert all(item["search_type"] == "semantic" for item in results)
    assert all(item["score"] > 0 for item in results)


def test_repair_missing_dense(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "get_config", lambda: _cfg(tmp_path))
    monkeypatch.setattr(mod, "FileMemoryStore", _DummyStore)
    monkeypatch.setattr(mod, "atomic_write_json", lambda *a, **k: None)
    searcher = mod.SemanticMemorySearch()
    searcher._cache = {
        "doc::1": {"text": "a", "dense": None, "retry_count": 0, "updated_at": datetime.now().isoformat()},
        "query::abc": {"text": "q", "dense": None, "retry_count": 0, "updated_at": datetime.now().isoformat()},
    }

    monkeypatch.setattr(searcher, "_ollama_embedding", lambda text: [0.1, 0.2])
    report = searcher.repair_missing_dense(limit=5, include_queries=False)
    assert report["checked"] == 1
    assert report["repaired"] == 1
    assert searcher._cache["doc::1"]["dense"] == [0.1, 0.2]


def test_load_cache_from_invalid_json_returns_empty(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    cache_file = cfg["memory_dir"] / "semantic_cache.json"
    cache_file.write_text("{broken-json", encoding="utf-8")
    monkeypatch.setattr(mod, "get_config", lambda: cfg)
    monkeypatch.setattr(mod, "FileMemoryStore", _DummyStore)

    searcher = mod.SemanticMemorySearch()
    assert searcher._cache == {}
    # ensure search still works from sparse fallback
    monkeypatch.setattr(searcher, "_ollama_embedding", lambda text: None)
    results = searcher.search("routing", top_k=1)
    assert isinstance(results, list)
