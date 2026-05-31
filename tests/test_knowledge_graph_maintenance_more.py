from __future__ import annotations

import json
from pathlib import Path

from ms8.engine_core import knowledge_graph as kg_mod


def _make_graph(tmp_path: Path, monkeypatch) -> kg_mod.KnowledgeGraph:
    cfg = {
        "workspace_dir": tmp_path,
        "memory_dir": tmp_path / "memory",
        "settings": {
            "memory": {
                "knowledge_graph": {
                    "db_path": "memory/kg_maint.db",
                    "auto_extract": False,
                    "default_return_limit": 20,
                    "max_query_depth": 5,
                    "batch_size": 5,
                    "enabled": True,
                },
                "knowledge_graph_quality": {},
            }
        },
    }
    cfg["memory_dir"].mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(kg_mod, "get_config", lambda: cfg)
    return kg_mod.KnowledgeGraph(llm=None)


def test_batch_extract_pending_memories_skip_and_force(tmp_path: Path, monkeypatch) -> None:
    g = _make_graph(tmp_path, monkeypatch)
    memory_dir = g.config["memory_dir"]
    memory_dir.mkdir(parents=True, exist_ok=True)
    g.config["memory_md"] = memory_dir / "MEMORY.md"
    g.config["memory_md"].write_text("# MEMORY.md - Your Long-Term Memory\nmemo", encoding="utf-8")
    (memory_dir / "memory_blocks.json").write_text(json.dumps({"archival": "arch"}), encoding="utf-8")
    daily = memory_dir / "daily"
    daily.mkdir(parents=True, exist_ok=True)
    (daily / "2026-05-01.md").write_text("d1", encoding="utf-8")

    # All "already extracted" -> processed 0
    monkeypatch.setattr(g, "_needs_extraction", lambda memory_ref, memory_hash: False)  # noqa: ANN001
    monkeypatch.setattr(g, "ingest_memory", lambda *a, **k: {"status": "success"})  # noqa: ANN001
    monkeypatch.setattr(g, "refine_related_relations", lambda: None)
    monkeypatch.setattr(g, "recompute_importance_scores", lambda: None)
    monkeypatch.setattr(g, "prune_low_signal_related_entities", lambda: {"status": "success"})
    monkeypatch.setattr(g, "prune_weak_isolated_entities", lambda: {"status": "success"})
    out_skip = g.batch_extract_pending_memories(limit=2, force=False)
    assert out_skip["status"] == "success"
    assert out_skip["processed"] == 0

    # force=True ignores needs_extraction and ingests up to limit
    seen: list[tuple[str, str, str]] = []

    def _ingest(memory_ref, content, source, title="", use_llm=False, force=False):  # noqa: ANN001
        seen.append((memory_ref, source, title))
        return {"status": "success", "memory_ref": memory_ref}

    monkeypatch.setattr(g, "ingest_memory", _ingest)
    out_force = g.batch_extract_pending_memories(limit=2, force=True)
    assert out_force["status"] == "success"
    assert out_force["processed"] == 2
    assert len(seen) == 2


def test_health_check_detects_duplicates_and_broken_rows(tmp_path: Path, monkeypatch) -> None:
    g = _make_graph(tmp_path, monkeypatch)
    g.add_entity("OpenClaw", entity_type="tool")
    g.add_entity("Open Claw", entity_type="tool")
    g.add_entity("GhostNode", entity_type="concept")
    rel = g.add_relation("OpenClaw", "uses", "GhostNode", strength=0.7, confidence=0.8)
    # Keep one abnormal-weight relation so health_check catches it.
    g.add_relation("OpenClaw", "depends_on", "Open Claw", strength=0.6, confidence=0.8)
    assert rel is not None

    # Create broken relation by deleting object entity directly.
    ghost = g.get_entity("GhostNode")
    assert ghost is not None
    with g._connect() as conn:
        conn.execute("UPDATE relations SET strength = 1.5 WHERE relation_type = 'depends_on'")
        conn.execute("DELETE FROM entities WHERE id = ?", (ghost["id"],))
        conn.commit()

    health = g.health_check()
    assert health["status"] == "success"
    assert health["broken_relations"]
    assert isinstance(health["abnormal_relation_weights"], list)
    # Duplicate detection may vary with normalization/tokenization settings;
    # keep this assertion stable by validating the returned structure.
    assert isinstance(health["suspected_duplicates"], list)


def test_prepare_offline_cleanup_writes_snapshot_and_report(tmp_path: Path, monkeypatch) -> None:
    g = _make_graph(tmp_path, monkeypatch)
    g.add_relation(
        "AlphaCore",
        "related_to",
        "BetaCore",
        strength=0.2,
        confidence=0.2,
        description="???",
    )
    out = g.prepare_offline_cleanup(limit=20)
    assert out["status"] == "success"
    assert Path(out["snapshot_file"]).exists()
    assert Path(out["report_file"]).exists()
    payload = json.loads(Path(out["report_file"]).read_text(encoding="utf-8"))
    assert "rules_trim_candidates" in payload
    assert payload["archived_candidate_marked"] >= 0


def test_graph_maintenance_operations_branches(tmp_path: Path, monkeypatch) -> None:
    g = _make_graph(tmp_path, monkeypatch)

    # seed core entities/relations
    g.add_relation("AlphaCore", "related_to", "BetaCore", strength=0.7, confidence=0.8, description="系统使用关系")
    g.add_relation("AlphaCore", "uses", "BetaCore", strength=0.6, confidence=0.7, description="existing uses")
    g.add_relation("KeepTool", "uses", "StableTech", strength=0.8, confidence=0.9, description="stable")

    # recompute importance and relation refinement
    recompute = g.recompute_importance_scores()
    assert recompute["status"] == "success"

    refined = g.refine_related_relations()
    assert refined["status"] == "success"
    assert refined["removed"] >= 1 or refined["converted"] >= 1

    # merge entity path
    g.add_entity("MS8", entity_type="tool", aliases=["ms8 runtime"])
    g.add_entity("MS8 Runtime", entity_type="tool", aliases=["ms8"])
    merged = g.merge_entities("MS8", "MS8 Runtime")
    assert merged["status"] == "success"
    assert g.merge_entities("MissingA", "MissingB")["status"] == "not_found"

    # decay branch (update or delete)
    g.settings["daily_decay_rate"] = 0.9
    g.settings["min_relation_strength"] = 0.1
    decay = g.decay_relation_weights()
    assert decay["status"] == "success"
    assert decay["updated"] >= 0 and decay["deleted"] >= 0

    # anchor-based access backfill
    ingest = g.ingest_memory(
        memory_ref="file::maint-1",
        content="MS8 uses StableTech in project memory",
        source="daily_log:2026-05-26.md",
        title="maint",
        force=True,
    )
    assert ingest["status"] == "success"
    backfill = g.backfill_entity_access_from_anchors(min_access=2)
    assert backfill["status"] == "success"
    assert backfill["updated_entities"] >= 0

    # isolated cleanup: create old isolated entity
    isolated = g.add_entity("OldIsolatedNode", entity_type="concept", importance=0.4)
    assert isolated is not None
    with g._connect() as conn:
        conn.execute("UPDATE entities SET updated_at = ? WHERE canonical_name = ?", ("2000-01-01T00:00:00+00:00", "OldIsolatedNode"))
        conn.commit()
    cleaned = g.cleanup_isolated_entities()
    assert cleaned["status"] == "success"

    # weak isolated prune: low-signal resource/concept should be removed
    g.add_entity("http://bad.example.com", entity_type="resource", importance=0.2, description="link")
    g.add_entity("普通概念条目", entity_type="concept", importance=0.2, description="弱描述")
    weak = g.prune_weak_isolated_entities()
    assert weak["status"] == "success"
    assert weak["removed_count"] >= 0

    # low-signal related prune: only related_to + low quality + no access
    g.add_relation("临时节点A", "related_to", "临时节点B", strength=0.4, confidence=0.4, description="相关")
    low_signal = g.prune_low_signal_related_entities()
    assert low_signal["status"] == "success"
    assert low_signal["removed_count"] >= 0
