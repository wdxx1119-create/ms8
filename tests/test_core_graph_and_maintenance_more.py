from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from ms8.engine_core.core import MemoryCore


def _core(tmp_path: Path) -> MemoryCore:
    c = MemoryCore.__new__(MemoryCore)
    c.config = {"memory_dir": tmp_path / "memory"}
    c._graph_enabled = lambda: True  # type: ignore[method-assign]
    c._dispatch_graph_batch_extract = lambda force=False: None  # type: ignore[method-assign]
    c.reindex_memory = lambda: None  # type: ignore[method-assign]
    c._run_maintenance_policy = lambda force=True: {"status": "ok", "force": bool(force)}  # type: ignore[method-assign]
    return c


def test_get_graph_context_and_augmented_context(tmp_path: Path) -> None:
    c = _core(tmp_path)
    c.get_context_with_blocks = lambda: "## blocks"  # type: ignore[method-assign]
    c.get_response_memory_context = lambda message: {"context": "## mem"}  # type: ignore[method-assign]
    c.knowledge_graph = SimpleNamespace(build_context_for_message=lambda message, limit=5: {"text": "## graph", "entities": ["a"]})
    out = c.get_graph_context("hello", limit=3)
    assert out["text"] == "## graph"
    merged = c.get_augmented_context("hello")
    assert "## blocks" in merged and "## mem" in merged and "## graph" in merged


def test_get_graph_context_disabled_and_error(tmp_path: Path) -> None:
    c = _core(tmp_path)
    c._graph_enabled = lambda: False  # type: ignore[method-assign]
    assert c.get_graph_context("x")["enabled"] is False

    c._graph_enabled = lambda: True  # type: ignore[method-assign]
    c.knowledge_graph = SimpleNamespace(build_context_for_message=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    out = c.get_graph_context("x")
    assert out["enabled"] is False
    assert "boom" in out["error"]


def test_governance_report_with_core_metrics_error(tmp_path: Path) -> None:
    c = _core(tmp_path)
    c.governance = SimpleNamespace(report=lambda limit=20: {"status": "ok", "limit": limit})
    c.monitoring = SimpleNamespace(status=lambda: (_ for _ in ()).throw(RuntimeError("mfail")))
    out = c.get_governance_report(limit=7)
    assert out["status"] == "ok"
    assert "core_metrics_error" in out


def test_trigger_memory_tiering_variants(tmp_path: Path) -> None:
    c = _core(tmp_path)
    c.learning = None
    assert c.trigger_memory_tiering()["status"] == "disabled"

    moved_calls = {"reindex": 0, "dispatch": 0}
    c.learning = SimpleNamespace(build_memory_tiering_plan=lambda: [{"from": "a", "to": "b"}])
    c.maintenance = SimpleNamespace(apply_tiering_plan=lambda plan, owner="maintenance": {"moved": ["f1"]})
    c.reindex_memory = lambda: moved_calls.__setitem__("reindex", moved_calls["reindex"] + 1)  # type: ignore[method-assign]
    c._dispatch_graph_batch_extract = lambda force=False: moved_calls.__setitem__("dispatch", moved_calls["dispatch"] + int(bool(force)))  # type: ignore[method-assign]
    out = c.trigger_memory_tiering()
    assert out["status"] == "success"
    assert moved_calls["reindex"] == 1
    assert moved_calls["dispatch"] == 1

    c.maintenance = SimpleNamespace(apply_tiering_plan=lambda plan, owner="maintenance": {"moved": []})
    moved_calls["reindex"] = 0
    moved_calls["dispatch"] = 0
    out2 = c.trigger_memory_tiering()
    assert out2["status"] == "success"
    assert moved_calls["reindex"] == 0
    assert moved_calls["dispatch"] == 0


def test_advanced_insight_and_maintenance_wrappers(tmp_path: Path) -> None:
    c = _core(tmp_path)
    c.context_understanding = SimpleNamespace(understandings={"a": 1})
    c.pattern_recognition = SimpleNamespace(patterns={"p": 1, "q": 2})
    c._advanced_insight_count = 9
    st = c.get_advanced_insight_status()
    assert st["enabled"] is True
    assert st["context_records"] == 1
    assert st["pattern_records"] == 2

    c.context_understanding = SimpleNamespace(understandings=None)
    c.pattern_recognition = SimpleNamespace(patterns=None)
    st2 = c.get_advanced_insight_status()
    assert st2["context_records"] == 0
    assert st2["pattern_records"] == 0

    c.maintenance = SimpleNamespace(
        run_maintenance=lambda force=True: {"status": "ok", "force": bool(force)},
        run_restore_drill=lambda: {"status": "ok", "type": "restore_drill"},
    )
    merged = c.run_maintenance_now(force=False)
    assert merged["maintenance"]["force"] is False
    assert merged["policy"]["status"] == "ok"
    assert c.run_restore_drill()["type"] == "restore_drill"
