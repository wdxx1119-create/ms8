from __future__ import annotations

import sqlite3
from pathlib import Path

from ms8.engine_core import sqlite_store as mod


def _build_config(tmp_path: Path, *, with_kg: bool) -> dict:
    db_path = tmp_path / "memory.db"
    cfg: dict = {
        "workspace_dir": tmp_path,
        "settings": {
            "memory": {
                "long_term": {"path": str(db_path)},
                "knowledge_graph": {},
            }
        },
    }
    if with_kg:
        cfg["settings"]["memory"]["knowledge_graph"]["db_path"] = str(tmp_path / "kg.db")
    return cfg


def _create_kg_schema(path: Path) -> None:
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS entities (
            id INTEGER PRIMARY KEY,
            canonical_name TEXT,
            name_key TEXT UNIQUE,
            entity_type TEXT,
            importance REAL,
            access_count INTEGER,
            created_at TEXT,
            updated_at TEXT,
            source_memory_ref TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS relations (
            id INTEGER PRIMARY KEY,
            subject_entity_id INTEGER,
            object_entity_id INTEGER,
            relation_type TEXT,
            strength REAL,
            confidence REAL,
            access_count INTEGER,
            created_at TEXT,
            updated_at TEXT,
            source_memory_ref TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def test_add_entity_and_relation_basics(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "get_config", lambda: _build_config(tmp_path, with_kg=False))
    store = mod.SQLiteMemoryStore()

    eid = store.add_entity("OpenClaw", "tool")
    assert eid > 0
    assert store.add_relation("OpenClaw", "uses", "MS8", 0.9) is True

    rels = store.get_entity_relations("OpenClaw")
    assert ("MS8", "uses", 0.9) in rels


def test_add_relation_updates_strength_when_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "get_config", lambda: _build_config(tmp_path, with_kg=False))
    store = mod.SQLiteMemoryStore()

    assert store.add_relation("A", "related_to", "B", 0.4) is True
    assert store.add_relation("A", "related_to", "B", 0.8) is True

    rels = store.get_entity_relations("A")
    assert ("B", "related_to", 0.8) in rels


def test_kg_bridge_entity_and_relation_mirror(tmp_path, monkeypatch):
    kg_path = tmp_path / "kg.db"
    _create_kg_schema(kg_path)
    monkeypatch.setattr(mod, "get_config", lambda: _build_config(tmp_path, with_kg=True))
    store = mod.SQLiteMemoryStore()

    assert store.add_relation("Claude", "supports", "MS8", 0.7) is True
    rels = store.get_entity_relations("Claude")
    assert any(item[0] == "MS8" and item[1] == "supports" for item in rels)

    conn = sqlite3.connect(kg_path)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM entities")
    entities_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM relations")
    relations_count = cur.fetchone()[0]
    conn.close()
    assert entities_count >= 2
    assert relations_count >= 1


def test_add_entity_returns_minus_one_on_sql_error(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "get_config", lambda: _build_config(tmp_path, with_kg=False))
    store = mod.SQLiteMemoryStore()

    class _BrokenCursor:
        def execute(self, *args, **kwargs):
            raise sqlite3.Error("boom")

        def fetchone(self):
            return None

    class _BrokenConn:
        def cursor(self):
            return _BrokenCursor()

        def commit(self):
            return None

        def close(self):
            return None

    class _BrokenCtx:
        def __enter__(self):
            return _BrokenConn()

        def __exit__(self, exc_type, exc, tb):
            return False

    original_connect = store._connect_db
    store._connect_db = lambda: _BrokenCtx()  # type: ignore[method-assign]
    try:
        assert store.add_entity("X", "unknown") == -1
    finally:
        store._connect_db = original_connect  # type: ignore[method-assign]


def test_update_entity_access_time_false_for_missing_entity(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "get_config", lambda: _build_config(tmp_path, with_kg=False))
    store = mod.SQLiteMemoryStore()

    assert store.update_entity_access_time("missing") is False


def test_cleanup_old_entities_removes_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "get_config", lambda: _build_config(tmp_path, with_kg=False))
    store = mod.SQLiteMemoryStore()
    store.add_entity("old", "tag")
    store.add_entity("new", "tag")

    with store._connect_db() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE entities SET last_accessed = '2000-01-01T00:00:00' WHERE name = 'old'")
        cur.execute("UPDATE entities SET last_accessed = datetime('now') WHERE name = 'new'")
        conn.commit()

    deleted = store.cleanup_old_entities(retention_days=1)
    assert deleted >= 1

    with store._connect_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM entities WHERE name = 'old'")
        assert cur.fetchone()[0] == 0
