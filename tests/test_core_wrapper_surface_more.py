from __future__ import annotations

import collections
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from ms8.engine_core.core import MemoryCore


def _core(tmp_path: Path) -> MemoryCore:
    c = MemoryCore.__new__(MemoryCore)
    c._recent_query_tokens = collections.deque(maxlen=24)
    c._utc_now = lambda: datetime(2026, 5, 25, tzinfo=timezone.utc)  # type: ignore[method-assign]
    c.config = {"workspace_dir": tmp_path, "memory_dir": tmp_path / "memory", "settings": {"memory": {}}}
    c._run_async = lambda x: x  # type: ignore[method-assign]
    c._graph_enabled = lambda: True  # type: ignore[method-assign]
    return c


def test_subagent_wrappers_and_background_fallbacks(tmp_path: Path) -> None:
    c = _core(tmp_path)
    c.subagents = SimpleNamespace(
        spawn=lambda name, task, background=False: {  # noqa: ANN001
            "status": "success",
            "name": name,
            "task": task,
            "background": background,
        },
        list_subagents=lambda: [{"name": "memory"}],
        list_background_tasks=lambda limit=20: [{"id": "b1", "limit": limit}],
        get_background_task_status=lambda task_id: {"status": "ok", "task_id": task_id},
        retry_background_task=lambda task_id: {"status": "success", "task_id": task_id},
        create_custom_subagent=lambda name, description, instructions, tools=None: {  # noqa: ANN001
            "status": "success",
            "name": name,
        },
    )
    c.skills = SimpleNamespace(
        learn_skill_from_trajectory=lambda tr, n, ins=None: {"status": "success", "name": n},  # noqa: ANN001
        list_skills=lambda: [{"name": "s1"}],
        load_skill=lambda n: f"skill:{n}",  # noqa: ANN001
        create_skill=lambda n, d, i, s, r=None: {"status": "success", "name": n},  # noqa: ANN001
    )
    c.memory_blocks = SimpleNamespace(get_block=lambda key: key.upper(), export_blocks=lambda: "BLOCKS")

    assert c.spawn_subagent("memory", "t", background=True)["status"] == "success"
    assert c.list_subagents() == [{"name": "memory"}]
    assert c.list_background_subagent_tasks(limit=3)[0]["limit"] == 3
    assert c.get_background_subagent_task("x")["task_id"] == "x"
    assert c.retry_background_subagent_task("y")["task_id"] == "y"
    assert c.create_subagent("n", "d", "i")["status"] == "success"
    assert c.learn_skill([], "k")["status"] == "success"
    assert c.list_skills() == [{"name": "s1"}]
    assert c.load_skill("abc") == "skill:abc"
    assert c.create_skill("x", "d", "i")["status"] == "success"
    assert c.get_memory_blocks()["human"] == "HUMAN"
    assert c.get_context_with_blocks() == "BLOCKS"

    c.subagents = SimpleNamespace()
    assert c.list_background_subagent_tasks() == []
    assert c.get_background_subagent_task("x")["status"] == "error"
    assert c.retry_background_subagent_task("x")["status"] == "error"


def test_graph_wrappers_disabled_and_success_paths(tmp_path: Path) -> None:
    c = _core(tmp_path)
    c._graph_enabled = lambda: False  # type: ignore[method-assign]
    c.knowledge_graph = None
    assert c.search_graph_entities("x") == []
    assert c.list_graph_relations() == []
    assert c.get_graph_neighbors("a") == []
    assert c.get_graph_related_entities("a") == []
    assert c.find_graph_path("a", "b")["status"] == "disabled"
    assert c.batch_extract_knowledge_graph()["status"] == "disabled"
    assert c.get_knowledge_graph_stats()["status"] == "disabled"
    assert c.get_knowledge_graph_timeline()["status"] == "disabled"
    assert c.get_knowledge_graph_health()["status"] == "disabled"
    assert c.run_knowledge_graph_maintenance()["status"] == "disabled"
    assert c.prepare_graph_offline_cleanup()["status"] == "disabled"
    assert c.get_graph_context("msg")["enabled"] is False

    c._graph_enabled = lambda: True  # type: ignore[method-assign]
    c.knowledge_graph = SimpleNamespace(
        build_context_for_message=lambda message, limit=5: {"enabled": True, "text": f"{message}:{limit}", "entities": []},  # noqa: ANN001,E501
        search_entities=lambda q, entity_type=None, limit=10: [{"q": q, "limit": limit}],  # noqa: ANN001
        list_relations=lambda *a, **k: [{"ok": True}],  # noqa: ANN001
        get_neighbors=lambda *a, **k: [{"n": 1}],  # noqa: ANN001
        related_entities=lambda *a, **k: [{"r": 1}],  # noqa: ANN001
        shortest_path=lambda *a, **k: {"status": "success", "path": ["a", "b"]},  # noqa: ANN001
        batch_extract_pending_memories=lambda limit=None, force=False: {"status": "success", "limit": limit, "force": force},  # noqa: ANN001,E501
        stats=lambda: {"status": "success"},
        timeline=lambda days=7, limit=10: {"status": "success", "days": days},
        health_check=lambda: {"status": "ok"},
        decay_relation_weights=lambda: {"status": "ok"},
        cleanup_isolated_entities=lambda: {"status": "ok"},
        prepare_offline_cleanup=lambda limit=500: {"status": "success", "limit": limit},
    )
    assert c.get_graph_context("m", limit=2)["text"] == "m:2"
    assert c.search_graph_entities("x")[0]["q"] == "x"
    assert c.list_graph_relations()[0]["ok"] is True
    assert c.get_graph_neighbors("a")[0]["n"] == 1
    assert c.get_graph_related_entities("a")[0]["r"] == 1
    assert c.find_graph_path("a", "b")["status"] == "success"
    assert c.batch_extract_knowledge_graph(limit=1, force=True)["force"] is True
    assert c.get_knowledge_graph_stats()["status"] == "success"
    assert c.get_knowledge_graph_timeline(days=3)["days"] == 3
    assert c.get_knowledge_graph_health()["status"] == "ok"
    assert c.run_knowledge_graph_maintenance()["status"] == "success"
    assert c.prepare_graph_offline_cleanup(limit=9)["limit"] == 9

    c.knowledge_graph = SimpleNamespace(build_context_for_message=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("kg")))  # noqa: ANN001,E501
    assert c.get_graph_context("m")["enabled"] is False


def test_governance_tiering_reflection_and_augmented_context(tmp_path: Path) -> None:
    c = _core(tmp_path)
    c.governance = SimpleNamespace(report=lambda limit=20: {"status": "ok", "limit": limit})
    c.monitoring = SimpleNamespace(status=lambda: {"core_metrics": {"a": 1}})
    report = c.get_governance_report(limit=7)
    assert report["status"] == "ok"
    assert report["core_metrics"]["a"] == 1

    c.monitoring = SimpleNamespace(status=lambda: (_ for _ in ()).throw(RuntimeError("m")))
    report_err = c.get_governance_report(limit=7)
    assert "core_metrics_error" in report_err

    c.learning = None
    assert c.trigger_memory_tiering()["status"] == "disabled"
    c.learning = SimpleNamespace(
        build_memory_tiering_plan=lambda: [{"f": "a"}],
        trigger_daily_learning=lambda: None,
        trigger_weekly_compression=lambda preview_only=True: {"status": "success", "preview_only": preview_only},
    )
    c.maintenance = SimpleNamespace(apply_tiering_plan=lambda plan, owner="maintenance": {"moved": ["x.md"]})  # noqa: ANN001,E501
    called: list[tuple[str, bool]] = []
    c.reindex_memory = lambda: called.append(("reindex", True))  # type: ignore[method-assign]
    c._dispatch_graph_batch_extract = lambda force=False: called.append(("kg", force))  # type: ignore[method-assign]
    out = c.trigger_memory_tiering()
    assert out["status"] == "success"
    assert ("reindex", True) in called and ("kg", True) in called
    assert c.preview_weekly_compression()["status"] == "success"

    c.learning = SimpleNamespace(trigger_daily_learning=lambda: (_ for _ in ()).throw(RuntimeError("L")))
    c.subagents = SimpleNamespace(spawn=lambda *a, **k: {"status": "success"})  # noqa: ANN001
    c._graph_enabled = lambda: True  # type: ignore[method-assign]
    c.run_knowledge_graph_maintenance = lambda: {"status": "success"}  # type: ignore[method-assign]
    ref = c.trigger_reflection()
    assert ref["status"] == "success"
    assert ref["learning_triggered"] is False
    assert "knowledge_graph_maintenance" in ref

    c.get_response_memory_context = lambda msg: {"context": f"CTX:{msg}"}  # type: ignore[method-assign]
    c.get_graph_context = lambda msg, limit=5: {"text": f"GRAPH:{msg}:{limit}"}  # type: ignore[method-assign]
    c.get_context_with_blocks = lambda: "BLOCKS"  # type: ignore[method-assign]
    text = c.get_augmented_context("hello", include_blocks=True, graph_limit=3)
    assert "BLOCKS" in text and "CTX:hello" in text and "GRAPH:hello:3" in text
