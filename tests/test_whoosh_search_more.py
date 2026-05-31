from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ms8.engine_core import whoosh_search as ws_mod


class _DummyStore:
    def __init__(self, changed: bool = False, text: str = "") -> None:
        self._changed = changed
        self._text = text or "default memory text"
        self.hash_loaded = False

    def has_memory_md_changed(self) -> bool:
        return self._changed

    def read_memory_md(self) -> str:
        return self._text

    def _load_memory_md_hash(self) -> None:
        self.hash_loaded = True


def _mk_cfg(tmp_path: Path) -> dict:
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    memory_md = tmp_path / "MEMORY.md"
    memory_md.write_text("memory body", encoding="utf-8")
    return {
        "settings": {"memory": {"keyword": {"index_dir": str(tmp_path / "idx")}}},
        "memory_dir": memory_dir,
        "daily_dir": str(memory_dir / "daily"),
        "memory_md": memory_md,
    }


def test_build_fallback_query_variants(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ws_mod, "get_config", lambda: _mk_cfg(tmp_path))
    monkeypatch.setattr(ws_mod, "FileMemoryStore", lambda: _DummyStore(changed=False))
    monkeypatch.setattr(ws_mod, "list_daily_log_files", lambda _m, _d: [])
    s = ws_mod.WhooshSearch()
    assert s._build_fallback_query("") == ""
    assert s._build_fallback_query("a b") == "a OR b"
    cjk_q = s._build_fallback_query("中文测试")
    assert "中" in cjk_q and "中文" in cjk_q


def test_index_and_search_with_filters_and_fallback(tmp_path: Path, monkeypatch) -> None:
    cfg = _mk_cfg(tmp_path)
    daily_dir = Path(cfg["daily_dir"])
    daily_dir.mkdir(parents=True, exist_ok=True)
    daily_log = daily_dir / "2026-05-20-notes.md"
    daily_log.write_text("项目推进 风险评审", encoding="utf-8")

    store = _DummyStore(changed=False, text="长期记忆：系统治理与压缩策略")
    monkeypatch.setattr(ws_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(ws_mod, "FileMemoryStore", lambda: store)
    monkeypatch.setattr(ws_mod, "list_daily_log_files", lambda _m, _d: [daily_log])

    s = ws_mod.WhooshSearch()
    s.reindex_all()

    results = s.search("项目推进", top_k=5)
    assert isinstance(results, list)
    assert any("项目推进" in r["content"] for r in results)

    filtered = s.search("项目推进", source_filter=f"daily_log:{daily_log.name}", top_k=5)
    assert all(r["source"] == f"daily_log:{daily_log.name}" for r in filtered)

    dr = s.search("项目推进", date_from=datetime(2026, 5, 1), date_to=datetime(2026, 5, 31), top_k=5)
    assert isinstance(dr, list)
    assert s.has_index() is True


def test_init_reindexes_on_memory_changed(tmp_path: Path, monkeypatch) -> None:
    cfg = _mk_cfg(tmp_path)
    store = _DummyStore(changed=True, text="changed")
    monkeypatch.setattr(ws_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(ws_mod, "FileMemoryStore", lambda: store)
    monkeypatch.setattr(ws_mod, "list_daily_log_files", lambda _m, _d: [])
    ws_mod.WhooshSearch()
    # if changed=True, _init_index should call reindex_all and load hash
    assert store.hash_loaded is True
