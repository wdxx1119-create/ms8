from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from ms8.engine_core.core import MemoryCore


class _KG:
    def is_enabled(self):
        return True

    def search_entities(self, q, entity_type=None, limit=10):
        return [{"q": q, "entity_type": entity_type, "limit": limit}]

    def list_relations(self, entity_name, relation_type=None, direction="both", limit=10):
        return [{"entity_name": entity_name, "relation_type": relation_type, "direction": direction, "limit": limit}]

    def get_neighbors(self, entity_name, depth=2, relation_type=None, limit=10):
        return [{"entity_name": entity_name, "depth": depth, "relation_type": relation_type, "limit": limit}]

    def related_entities(self, entity_name, limit=10):
        return [{"entity_name": entity_name, "limit": limit}]

    def shortest_path(self, start, end, max_depth=3):
        return {"status": "success", "path": [start, end], "max_depth": max_depth}

    def batch_extract_pending_memories(self, limit=None, force=False):
        return {"status": "success", "limit": limit, "force": force}

    def stats(self):
        return {"entities": 1}

    def timeline(self, days=7, limit=10):
        return {"days": days, "limit": limit}

    def health_check(self):
        return {"status": "ok"}

    def decay_relation_weights(self):
        return {"decayed": 1}

    def cleanup_isolated_entities(self):
        return {"removed": 1}

    def prepare_offline_cleanup(self, limit=500):
        return {"status": "ready", "limit": limit}


def _core(tmp_path: Path) -> MemoryCore:
    core = MemoryCore.__new__(MemoryCore)
    core.config = {
        "memory_dir": tmp_path,
        "settings": {
            "memory": {"knowledge_graph": {"enabled": True}, "skills_system": {}},
        },
    }
    core.knowledge_graph = _KG()
    core.skill_installer = SimpleNamespace(
        install_from_github=lambda url, scope="project": {"status": "success", "url": url, "scope": scope},
        install_from_registry=lambda sid, scope="project": {"status": "success", "skill_id": sid, "scope": scope},
        install_from_file=lambda path, scope="project": {"status": "success", "path": path, "scope": scope},
        uninstall=lambda name, scope="project": {"status": "success", "name": name, "scope": scope},
        list_installed=lambda: [{"name": "x"}],
        check_updates=lambda: [{"name": "x", "update": True}],
        update_skill=lambda name: {"status": "success", "name": name},
    )
    core.built_in_skills = SimpleNamespace(
        get_built_in_skills=lambda: [{"name": "a"}],
        install_built_in=lambda n: {"status": "success", "name": n},
        install_all_built_in=lambda: {"status": "success", "count": 1},
    )
    core.skill_discovery = SimpleNamespace(
        get_relevant_skills=lambda context, top_k: [{"context": context, "top_k": top_k}],
        get_system_prompt_injection=lambda: "PROMPT",
    )
    core.skills = SimpleNamespace(load_skill=lambda name: "content" if name == "ok" else "")
    core.skill_registry = SimpleNamespace(add_registry=lambda n, u, t: {"name": n, "url": u, "type": t})
    core.github_discovery = SimpleNamespace(
        search_skills=lambda **kwargs: [{"repository": "o/r", "path": "skills/s1", "name": "s1"}],
        get_trending_skills=lambda days, limit: [{"days": days, "limit": limit}],
        get_skill_recommendations=lambda context, limit: [{"context": context, "limit": limit}],
        get_skill_catalog=lambda org: {"org": org, "skills": []},
    )
    core.skill_search_index = SimpleNamespace(
        search=lambda **kwargs: [{"query": kwargs.get("query")}],
        get_categories=lambda: ["cat"],
        get_tags=lambda: ["tag"],
        suggest=lambda p, _field, _limit: [p + "1"],
        get_index_stats=lambda: {"count": 1},
        clear_index=lambda: None,
        update_index=lambda skills: len(skills),
    )
    return core


def test_graph_wrappers_enabled_and_disabled(tmp_path: Path):
    core = _core(tmp_path)
    assert core.search_graph_entities("k")[0]["q"] == "k"
    assert core.list_graph_relations(entity_name="e")[0]["entity_name"] == "e"
    assert core.get_graph_neighbors("e")[0]["depth"] == 2
    assert core.get_graph_related_entities("e")[0]["entity_name"] == "e"
    assert core.find_graph_path("a", "b")["status"] == "success"
    assert core.batch_extract_knowledge_graph(limit=3, force=True)["force"] is True
    assert core.get_knowledge_graph_stats()["entities"] == 1
    assert core.get_knowledge_graph_timeline(days=3)["days"] == 3
    assert core.get_knowledge_graph_health()["status"] == "ok"
    maintenance = core.run_knowledge_graph_maintenance()
    assert maintenance["status"] == "success"
    assert core.prepare_graph_offline_cleanup(limit=7)["limit"] == 7

    core.knowledge_graph = None
    assert core.search_graph_entities("x") == []
    assert core.find_graph_path("a", "b")["status"] == "disabled"
    assert core.get_knowledge_graph_stats()["status"] == "disabled"


def test_skill_wrapper_methods(tmp_path: Path):
    core = _core(tmp_path)
    assert core.install_skill_from_github("u")["status"] == "success"
    assert core.install_skill_from_registry("@x")["status"] == "success"
    assert core.install_skill_from_file("f")["status"] == "success"
    assert core.uninstall_skill("x")["status"] == "success"
    assert core.list_installed_skills()[0]["name"] == "x"
    assert core.get_built_in_skills()[0]["name"] == "a"
    assert core.install_built_in_skill("a")["status"] == "success"
    assert core.install_all_built_in_skills()["count"] == 1
    assert core.get_relevant_skills("ctx", top_k=2)[0]["top_k"] == 2
    assert core.get_system_prompt_with_skills() == "PROMPT"
    assert core.load_skill_with_tool("ok")["status"] == "success"
    assert core.load_skill_with_tool("missing")["status"] == "error"
    assert core.add_skill_registry("r", "u", "github")["status"] == "success"
    assert core.check_skill_updates()[0]["update"] is True
    assert core.update_skill("x")["status"] == "success"


def test_online_skill_gate_and_refresh(tmp_path: Path):
    core = _core(tmp_path)
    cfg = core.config["settings"]["memory"]["skills_system"]

    # disabled by default
    assert core.search_github_skills("x") == []
    assert core.get_trending_skills() == []
    assert core.get_skill_recommendations("ctx") == []
    assert core.get_github_skill_catalog()["status"] == "disabled"
    assert core.install_skill_from_github_search("s1")["status"] == "disabled"
    assert core.refresh_skill_index()["status"] == "disabled"

    # enable discovery + marketplace only (auto install still gated)
    cfg["github_enabled"] = True
    cfg["marketplace_enabled"] = True
    assert core.search_github_skills(query="s1")[0]["name"] == "s1"
    assert core.get_trending_skills(days=9)[0]["days"] == 9
    assert core.get_skill_recommendations("ctx")[0]["context"] == "ctx"
    assert core.get_github_skill_catalog("org")["org"] == "org"
    assert core.install_skill_from_github_search("s1")["status"] == "disabled"

    # enable auto-install
    cfg["auto_install_enabled"] = True
    installed = core.install_skill_from_github_search("s1")
    assert installed["status"] == "success"
    refreshed = core.refresh_skill_index()
    assert refreshed["status"] == "success"
