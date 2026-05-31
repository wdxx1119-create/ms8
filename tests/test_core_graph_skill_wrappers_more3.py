from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from ms8.engine_core.core import MemoryCore


def _core(tmp_path: Path) -> MemoryCore:
    c = MemoryCore.__new__(MemoryCore)
    c.config = {"memory_dir": tmp_path / "memory"}
    c._graph_enabled = lambda: True  # type: ignore[method-assign]
    return c


def test_graph_wrappers_disabled_and_enabled(tmp_path: Path) -> None:
    c = _core(tmp_path)
    c._graph_enabled = lambda: False  # type: ignore[method-assign]
    assert c.search_graph_entities("a") == []
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

    c._graph_enabled = lambda: True  # type: ignore[method-assign]
    c.knowledge_graph = None
    assert c.search_graph_entities("a") == []
    assert c.find_graph_path("a", "b")["status"] == "disabled"

    c.knowledge_graph = SimpleNamespace(
        search_entities=lambda q, entity_type=None, limit=10: [{"q": q, "type": entity_type, "limit": limit}],
        list_relations=lambda e=None, relation_type=None, direction="both", limit=10: [{"e": e, "rt": relation_type, "d": direction, "l": limit}],
        get_neighbors=lambda e, depth=2, relation_type=None, limit=10: [{"e": e, "depth": depth, "rt": relation_type, "l": limit}],
        related_entities=lambda e, limit=10: [{"e": e, "l": limit}],
        shortest_path=lambda a, b, max_depth=3: {"status": "success", "path": [a, b], "max_depth": max_depth},
        batch_extract_pending_memories=lambda limit=None, force=False: {"status": "success", "limit": limit, "force": force},
        stats=lambda: {"status": "ok"},
        timeline=lambda days=7, limit=10: {"days": days, "limit": limit},
        health_check=lambda: {"status": "healthy"},
        decay_relation_weights=lambda: {"decayed": 1},
        cleanup_isolated_entities=lambda: {"removed": 2},
        prepare_offline_cleanup=lambda limit=500: {"status": "prepared", "limit": limit},
    )
    assert c.search_graph_entities("x", entity_type="tool", limit=2)[0]["q"] == "x"
    assert c.list_graph_relations(entity_name="n", relation_type="uses", direction="out", limit=3)[0]["d"] == "out"
    assert c.get_graph_neighbors("n", depth=4, relation_type="dep", limit=6)[0]["depth"] == 4
    assert c.get_graph_related_entities("n", limit=8)[0]["l"] == 8
    assert c.find_graph_path("a", "b", max_depth=9)["max_depth"] == 9
    assert c.batch_extract_knowledge_graph(limit=5, force=True)["force"] is True
    assert c.get_knowledge_graph_stats()["status"] == "ok"
    assert c.get_knowledge_graph_timeline(days=12, limit=4)["days"] == 12
    assert c.get_knowledge_graph_health()["status"] == "healthy"
    maint = c.run_knowledge_graph_maintenance()
    assert maint["status"] == "success"
    assert maint["decay"]["decayed"] == 1
    assert maint["cleanup"]["removed"] == 2
    assert c.prepare_graph_offline_cleanup(limit=123)["limit"] == 123


def test_skill_system_wrappers(tmp_path: Path) -> None:
    c = _core(tmp_path)
    c.skill_installer = SimpleNamespace(
        install_from_github=lambda url, scope: {"status": "success", "url": url, "scope": scope},
        install_from_registry=lambda skill_id, scope: {"status": "success", "skill_id": skill_id, "scope": scope},
        install_from_file=lambda fp, scope: {"status": "success", "file_path": fp, "scope": scope},
        uninstall=lambda name, scope: {"status": "success", "name": name, "scope": scope},
        list_installed=lambda: [{"name": "x"}],
    )
    c.built_in_skills = SimpleNamespace(
        get_built_in_skills=lambda: [{"name": "builtin"}],
        install_built_in=lambda name: {"status": "success", "name": name},
        install_all_built_in=lambda: {"status": "success", "count": 3},
    )
    c.skill_discovery = SimpleNamespace(
        get_relevant_skills=lambda context, top_k=3: [{"context": context, "top_k": top_k}],
        get_system_prompt_injection=lambda: "PROMPT",
    )
    c.skills = SimpleNamespace(load_skill=lambda name: "BODY" if name == "ok" else None)
    c.skill_registry = SimpleNamespace(add_registry=lambda n, u, t: {"name": n, "url": u, "type": t})

    assert c.install_skill_from_github("https://x", scope="project")["status"] == "success"
    assert c.install_skill_from_registry("@a/b", scope="agent")["scope"] == "agent"
    assert c.install_skill_from_file("/tmp/a", scope="global")["scope"] == "global"
    assert c.uninstall_skill("old", scope="project")["name"] == "old"
    assert c.list_installed_skills()[0]["name"] == "x"
    assert c.get_built_in_skills()[0]["name"] == "builtin"
    assert c.install_built_in_skill("python")["name"] == "python"
    assert c.install_all_built_in_skills()["count"] == 3
    assert c.get_relevant_skills("ctx", top_k=7)[0]["top_k"] == 7
    assert c.get_system_prompt_with_skills() == "PROMPT"
    assert c.load_skill_with_tool("ok")["status"] == "success"
    assert c.load_skill_with_tool("bad")["status"] == "error"
    reg = c.add_skill_registry("n", "u", "github")
    assert reg["status"] == "success"
    assert reg["registry"]["name"] == "n"
