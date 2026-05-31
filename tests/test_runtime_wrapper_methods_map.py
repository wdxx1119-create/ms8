from __future__ import annotations

from ms8 import runtime


def test_runtime_wrappers_call_expected_core_methods(monkeypatch) -> None:
    called: list[tuple[str, tuple, dict]] = []

    def _fake(name: str, *args, **kwargs):  # noqa: ANN002, ANN003
        called.append((name, args, kwargs))
        return {"ok": True, "method": name}

    monkeypatch.setattr(runtime, "_run_core_method", _fake)

    runtime.run_kg_batch_extract(limit=7, force=True)
    runtime.run_memory_tiering()
    runtime.run_graph_maintenance()
    runtime.run_reflection()
    runtime.run_synthetic_auto_confirm()
    runtime.list_archived_logs_runtime(limit=3)
    runtime.list_subagents_runtime()
    runtime.list_subagent_tasks_runtime(limit=9)
    runtime.run_validation_suite_runtime()
    runtime.backfill_auto_memory_ids_runtime()
    runtime.run_meta_cognition_runtime(period="weekly")
    runtime.get_context_with_blocks_runtime()
    runtime.get_augmented_context_runtime("hello", include_blocks=False, graph_limit=2)
    runtime.get_background_subagent_task_runtime("task-1")
    runtime.get_github_skill_catalog_runtime(org="openclaw")
    runtime.get_graph_context_runtime("q", limit=4)
    runtime.get_graph_related_entities_runtime("entity", limit=6)
    runtime.get_self_improvement_metrics_runtime()
    runtime.get_system_prompt_with_skills_runtime()
    runtime.graph_stats_runtime()
    runtime.graph_extract_runtime(limit=5, force=True)
    runtime.graph_maint_runtime()
    runtime.graph_repair_access_runtime(min_access=2)
    runtime.graph_search_entities_runtime("hello", entity_type="tool", limit=8)
    runtime.graph_list_relations_runtime(relation_type="related_to", limit=8)
    runtime.graph_neighbors_runtime("entity", depth=2, relation_type="related_to", limit=7)
    runtime.graph_path_runtime("a", "b", max_depth=4)
    runtime.graph_timeline_runtime(days=3, limit=6)
    runtime.graph_health_runtime()

    names = [x[0] for x in called]
    assert "batch_extract_knowledge_graph" in names
    assert "trigger_memory_tiering" in names
    assert "run_knowledge_graph_maintenance" in names
    assert "trigger_reflection" in names
    assert "auto_confirm_synthetic_candidates" in names
    assert "list_archived_logs" in names
    assert "list_subagents" in names
    assert "list_background_subagent_tasks" in names
    assert "run_validation_suite" in names
    assert "backfill_auto_memory_record_ids" in names
    assert "run_meta_cognition" in names
    assert "get_context_with_blocks" in names
    assert "get_augmented_context" in names
    assert "get_background_subagent_task" in names
    assert "get_github_skill_catalog" in names
    assert "get_graph_context" in names
    assert "get_graph_related_entities" in names
    assert "get_self_improvement_metrics" in names
    assert "get_system_prompt_with_skills" in names
    assert "get_knowledge_graph_stats" in names
    assert "batch_extract_knowledge_graph" in names
    assert "run_knowledge_graph_maintenance" in names
    assert "repair_graph_access_counts" in names
    assert "search_graph_entities" in names
    assert "list_graph_relations" in names
    assert "get_graph_neighbors" in names
    assert "find_graph_path" in names
    assert "get_knowledge_graph_timeline" in names
    assert "get_knowledge_graph_health" in names
