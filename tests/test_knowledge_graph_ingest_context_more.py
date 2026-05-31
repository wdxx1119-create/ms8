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
                    "db_path": "memory/kg_ingest_context.db",
                    "auto_extract": False,
                    "default_return_limit": 20,
                    "max_query_depth": 5,
                    "context_injection_enabled": True,
                    "context_injection_limit": 5,
                    "enabled": True,
                    "extraction_mode": "rule",
                },
                "knowledge_graph_quality": {},
            }
        },
    }
    cfg["memory_dir"].mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(kg_mod, "get_config", lambda: cfg)
    return kg_mod.KnowledgeGraph(llm=None)


def test_ingest_memory_status_paths(tmp_path: Path, monkeypatch) -> None:
    g = _make_graph(tmp_path, monkeypatch)

    g.settings["enabled"] = False
    assert g.ingest_memory("m1", "x", "s")["status"] == "disabled"

    g.settings["enabled"] = True
    assert g.ingest_memory("m2", "   ", "s")["status"] == "skipped"

    monkeypatch.setattr(g, "_needs_extraction", lambda *_a, **_k: False)
    out = g.ingest_memory("m3", "real content", "s", force=False)
    assert out["status"] == "skipped"
    assert out["reason"] == "already_extracted"


def test_ingest_memory_success_and_search_related(tmp_path: Path, monkeypatch) -> None:
    g = _make_graph(tmp_path, monkeypatch)

    extraction = kg_mod.GraphExtractionResult(
        entities=[
            {
                "name": "MS8",
                "type": "tool",
                "aliases": ["OpenClaw Memory"],
                "description": "memory runtime",
                "importance": 0.9,
                "quality": 0.95,
                "frequency": 3,
            },
            {
                "name": "Noise123456",
                "type": "concept",
                "aliases": [],
                "description": "",
                "importance": 0.1,
                "quality": 0.05,
                "frequency": 1,
            },
        ],
        relations=[
            {
                "subject": "MS8",
                "type": "uses",
                "object": "Ollama",
                "strength": 0.8,
                "description": "MS8 uses Ollama",
                "confidence": 0.9,
            }
        ],
        mode="rule",
    )
    monkeypatch.setattr(g, "extract_knowledge", lambda *_a, **_k: extraction)
    monkeypatch.setattr(g, "_needs_extraction", lambda *_a, **_k: True)

    out = g.ingest_memory(
        memory_ref="file::daily-1",
        content="MS8 uses Ollama for memory retrieval",
        source="daily_log:2026-05-26.md",
        title="Daily Log",
        force=False,
    )
    assert out["status"] == "success"
    assert out["entities_added"] >= 1
    assert out["relations_added"] >= 1

    rows = g.search_related_memories("MS8 memory", limit=5)
    assert rows
    assert rows[0]["search_type"] == "knowledge_graph"
    assert "matched_entities" in rows[0]


def test_build_context_for_message_paths(tmp_path: Path, monkeypatch) -> None:
    g = _make_graph(tmp_path, monkeypatch)
    monkeypatch.setattr(g, "_needs_extraction", lambda *_a, **_k: True)

    out = g.ingest_memory(
        memory_ref="file::daily-2",
        content="MS8 uses Ollama and SQLite in this project",
        source="daily_log:2026-05-26.md",
        title="Daily Log",
        force=True,
    )
    assert out["status"] == "success"

    # cost-signal branch prefers anchor snippets
    cost_ctx = g.build_context_for_message("有没有更便宜的MS8模型配置？", limit=3)
    assert cost_ctx["enabled"] is True
    assert isinstance(cost_ctx["entities"], list)
    assert "相关知识" in cost_ctx["text"] or cost_ctx["text"] == ""

    # generic keyword fallback branch
    generic_ctx = g.build_context_for_message("这个系统的工具配置怎么做？", limit=3)
    assert generic_ctx["enabled"] is True
    assert isinstance(generic_ctx["text"], str)

    g.settings["context_injection_enabled"] = False
    disabled = g.build_context_for_message("MS8", limit=2)
    assert disabled == {"enabled": False, "text": "", "entities": []}

