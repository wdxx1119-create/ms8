from __future__ import annotations

from pathlib import Path

import pytest


def _mk(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, whoosh: bool):
    from ms8.engine_core import skill_search_index as mod

    monkeypatch.setattr(mod, "WHOOSH_AVAILABLE", whoosh)
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(mod, "get_config", lambda: {"memory_dir": memory_dir})
    return mod


def test_fallback_index_search_suggest_stats(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    mod = _mk(monkeypatch, tmp_path, whoosh=False)
    idx = mod.SkillSearchIndex()
    assert idx.ix is None

    s1 = {"name": "react-ui", "description": "react front", "tags": ["react"], "category": "frontend", "stars": 9}
    s2 = {"name": "api-kit", "description": "rest api", "tags": ["api"], "category": "backend", "stars": 3}
    assert idx.index_skill(s1) is True
    assert idx.update_index([s2]) == 1

    out = idx.search("react", category="frontend", min_stars=5, limit=5)
    assert out and out[0]["name"] == "react-ui"
    assert idx.search("react", tags=["nope"]) == []

    assert "react-ui" in idx.suggest("re", field="name", limit=5)
    cats = idx.get_categories()
    tags = idx.get_tags()
    assert "frontend" in cats and "react" in tags
    stats = idx.get_index_stats()
    assert stats["whoosh_available"] is False
    assert idx.clear_index() is True
    assert idx.get_index_stats()["total_skills"] == 0


def test_fallback_get_full_content(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    mod = _mk(monkeypatch, tmp_path, whoosh=False)
    idx = mod.SkillSearchIndex()
    text = idx._get_full_content(
        {"name": "x", "description": "desc", "tags": ["a", "b"], "category": "c"}
    )
    assert "x" in text and "desc" in text and "a b" in text and "c" in text


def test_whoosh_branch_error_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    mod = _mk(monkeypatch, tmp_path, whoosh=True)

    class _Hit(dict):
        score = 1.0

    class _Reader:
        def fieldnames(self):
            return {"name", "category", "tags"}

        def field_texts(self, field):
            if field == "name":
                return ["alpha", "beta"]
            if field == "category":
                return ["frontend"]
            if field == "tags":
                return ["react ui"]
            return []

    class _Searcher:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def search(self, *_args, **_kwargs):
            return [_Hit(name="alpha", category="frontend", stars=5, rating=5, tags="react ui")]

        def reader(self):
            return _Reader()

        def doc_count(self):
            return 1

    class _IX:
        def writer(self):
            class _W:
                def add_document(self, **_):
                    return None

                def commit(self):
                    return None

            return _W()

        def searcher(self):
            return _Searcher()

    monkeypatch.setattr(mod.SkillSearchIndex, "_create_schema", lambda self: object())
    monkeypatch.setattr(mod.SkillSearchIndex, "_open_or_create_index", lambda self: _IX())
    monkeypatch.setattr(mod, "MultifieldParser", lambda *_a, **_k: type("P", (), {"parse": lambda self, q: q})())
    idx = mod.SkillSearchIndex()

    assert idx.index_skill({"name": "n", "description": "d", "tags": [], "created": "bad-date"}) is True
    found = idx.search("alpha", category="frontend", tags=["react"], min_stars=1, min_rating=1, limit=3)
    assert found and found[0]["name"] == "alpha"
    assert idx.suggest("a", field="name", limit=5) == ["alpha"]
    assert "frontend" in idx.get_categories()
    assert "react" in idx.get_tags()
    st = idx.get_index_stats()
    assert st["whoosh_available"] is True and st["total_skills"] == 1


def test_whoosh_error_fallbacks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    mod = _mk(monkeypatch, tmp_path, whoosh=True)

    class _BadIX:
        def writer(self):
            raise RuntimeError("writer fail")

        def searcher(self):
            raise RuntimeError("search fail")

    monkeypatch.setattr(mod.SkillSearchIndex, "_create_schema", lambda self: object())
    monkeypatch.setattr(mod.SkillSearchIndex, "_open_or_create_index", lambda self: _BadIX())
    idx = mod.SkillSearchIndex()

    assert idx.index_skill({"name": "x"}) is False
    assert idx.search("x") == []
    assert idx.suggest("x") == []
    assert idx.get_categories() == []
    assert idx.get_tags() == []
    stats = idx.get_index_stats()
    assert stats["whoosh_available"] is True and "error" in stats

