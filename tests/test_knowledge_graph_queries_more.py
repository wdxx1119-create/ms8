from __future__ import annotations

from pathlib import Path

from ms8.engine_core import knowledge_graph as kg_mod


def _make_graph(tmp_path: Path, monkeypatch) -> kg_mod.KnowledgeGraph:
    cfg = {
        "workspace_dir": tmp_path,
        "memory_dir": tmp_path / "memory",
        "settings": {
            "memory": {
                "knowledge_graph": {
                    "db_path": "memory/kg_query.db",
                    "auto_extract": False,
                    "default_return_limit": 20,
                    "max_query_depth": 5,
                }
            }
        },
    }
    cfg["memory_dir"].mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(kg_mod, "get_config", lambda: cfg)
    return kg_mod.KnowledgeGraph(llm=None)


def test_shortest_path_success_and_edge_cases(tmp_path: Path, monkeypatch) -> None:
    g = _make_graph(tmp_path, monkeypatch)
    g.add_relation("Alpha", "uses", "Beta", strength=0.8, confidence=0.8)
    g.add_relation("Beta", "depends_on", "Gamma", strength=0.7, confidence=0.9)

    p = g.shortest_path("Alpha", "Gamma", max_depth=3)
    assert p["status"] == "success"
    assert p["path"]

    same = g.shortest_path("Alpha", "Alpha")
    assert same["status"] == "success"
    assert same["path"] == ["Alpha"]

    not_found = g.shortest_path("Alpha", "ZZZ")
    assert not_found["status"] == "not_found"


def test_list_relations_directions_and_relation_count(tmp_path: Path, monkeypatch) -> None:
    g = _make_graph(tmp_path, monkeypatch)
    g.add_relation("MS8", "uses", "Ollama", strength=0.9, confidence=0.8)
    g.add_relation("OpenClaw", "uses", "MS8", strength=0.8, confidence=0.7)

    both = g.list_relations("MS8", direction="both")
    out = g.list_relations("MS8", direction="outgoing")
    inc = g.list_relations("MS8", direction="incoming")
    uses = g.list_relations("MS8", relation_type="uses")

    assert len(both) >= 2
    assert out
    assert inc
    assert uses
    assert g.relation_count("MS8") >= 2


def test_relation_between_and_gap_report(tmp_path: Path, monkeypatch) -> None:
    g = _make_graph(tmp_path, monkeypatch)
    g.add_entity("IsolatedNode", entity_type="concept", importance=0.9)
    g.add_relation("ToolA", "uses", "ToolB", strength=0.8, confidence=0.8)

    rel = g.relation_between("ToolA", "ToolB")
    assert rel
    none_rel = g.relation_between("ToolA", "Unknown")
    assert none_rel == []

    gaps = g.gap_report(min_importance=0.6, max_relations=0, limit=10)
    assert any(x["canonical_name"] == "IsolatedNode" for x in gaps)


def test_timeline_and_related_entities(tmp_path: Path, monkeypatch) -> None:
    g = _make_graph(tmp_path, monkeypatch)
    g.add_relation("RouterCore", "uses", "MemoryCore", strength=0.7, confidence=0.8)
    g.add_relation("MemoryCore", "depends_on", "SQLiteDB", strength=0.9, confidence=0.9)

    tl = g.timeline(days=30, limit=20)
    assert "entities" in tl and "relations" in tl
    assert isinstance(tl["entities"], list)
    assert isinstance(tl["relations"], list)

    related = g.related_entities("RouterCore", limit=5)
    assert isinstance(related, list)

    missing = g.related_entities("NoSuchEntity", limit=5)
    assert missing == []
