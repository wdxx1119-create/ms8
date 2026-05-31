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
                    "db_path": "memory/kg_test.db",
                    "auto_extract": False,
                    "default_return_limit": 20,
                    "max_query_depth": 4,
                }
            }
        },
    }
    cfg["memory_dir"].mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(kg_mod, "get_config", lambda: cfg)
    return kg_mod.KnowledgeGraph(llm=None)


def test_add_get_search_entity_and_alias(tmp_path: Path, monkeypatch) -> None:
    graph = _make_graph(tmp_path, monkeypatch)
    ent = graph.add_entity(
        "OpenClaw",
        entity_type="tool",
        aliases=["open claw", "OC"],
        description="memory runtime",
        importance=0.8,
    )
    assert ent is not None
    got = graph.get_entity("OpenClaw")
    assert got is not None
    assert got["canonical_name"] == "OpenClaw"
    assert "open claw" in [a.lower() for a in got["aliases"]]

    res = graph.search_entities("open")
    assert any(x["canonical_name"] == "OpenClaw" for x in res)


def test_add_relation_neighbors_and_stats(tmp_path: Path, monkeypatch) -> None:
    graph = _make_graph(tmp_path, monkeypatch)
    graph.add_entity("MS8", entity_type="tool", description="core")
    graph.add_entity("Ollama", entity_type="tool", description="provider")
    rel = graph.add_relation("MS8", "uses", "Ollama", strength=0.7, description="ms8 uses ollama", confidence=0.9)
    assert rel is not None

    rel_rows = graph.list_relations("MS8", limit=5)
    assert rel_rows
    assert rel_rows[0]["relation_type"] == "uses"

    between = graph.relation_between("MS8", "Ollama")
    assert between
    assert between[0]["relation_type"] == "uses"

    s = graph.stats()
    assert s["entity_total"] >= 2
    assert s["relation_total"] >= 1
    assert "knowledge_hubs" in s


def test_relation_merge_and_delete_entity(tmp_path: Path, monkeypatch) -> None:
    graph = _make_graph(tmp_path, monkeypatch)
    first = graph.add_relation("Alpha", "related_to", "Beta", strength=0.2, confidence=0.2, description="first")
    second = graph.add_relation("Alpha", "related_to", "Beta", strength=0.6, confidence=0.8, description="second")
    assert first is not None
    assert second is not None

    rels = graph.relation_between("Alpha", "Beta")
    assert len(rels) == 1
    assert float(rels[0]["strength"]) >= 0.6

    deleted = graph.delete_entity("Alpha")
    assert deleted["status"] == "success"
    assert graph.get_entity("Alpha") is None


def test_meaningless_entity_and_self_relation_blocked(tmp_path: Path, monkeypatch) -> None:
    graph = _make_graph(tmp_path, monkeypatch)
    assert graph.add_entity("123456", entity_type="concept") is None
    assert graph.add_relation("MS8", "related_to", "MS8") is None
