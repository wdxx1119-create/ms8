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
    c.config = {
        "workspace_dir": tmp_path,
        "memory_dir": tmp_path / "memory",
        "settings": {"memory": {"skills_system": {}}},
    }
    c._run_async = lambda x: x  # type: ignore[method-assign]
    c._dispatch_knowledge_graph_ingest = lambda *a, **k: None  # type: ignore[method-assign,assignment]
    return c


def test_skill_installer_and_registry_wrappers(tmp_path: Path) -> None:
    c = _core(tmp_path)
    c.skill_installer = SimpleNamespace(
        install_from_github=lambda url, scope="project": {"status": "success", "url": url, "scope": scope},  # noqa: ANN001,E501
        install_from_registry=lambda sid, scope="project": {"status": "success", "id": sid, "scope": scope},  # noqa: ANN001,E501
        install_from_file=lambda p, scope="project": {"status": "success", "path": p, "scope": scope},  # noqa: ANN001,E501
        uninstall=lambda n, scope="project": {"status": "success", "name": n, "scope": scope},  # noqa: ANN001,E501
        list_installed=lambda: [{"name": "x"}],
        check_updates=lambda: [{"name": "x", "update": True}],
        update_skill=lambda n: {"status": "success", "name": n},  # noqa: ANN001
    )
    c.built_in_skills = SimpleNamespace(
        get_built_in_skills=lambda: [{"name": "b"}],
        install_built_in=lambda n: {"status": "success", "name": n},  # noqa: ANN001
        install_all_built_in=lambda: {"status": "success"},
    )
    c.skill_discovery = SimpleNamespace(
        get_relevant_skills=lambda ctx, top_k=3: [{"ctx": ctx, "top_k": top_k}],  # noqa: ANN001
        get_system_prompt_injection=lambda: "PROMPT",
    )
    c.skills = SimpleNamespace(load_skill=lambda name: "CONTENT" if name == "ok" else None)  # noqa: ANN001
    c.skill_registry = SimpleNamespace(add_registry=lambda n, u, t="github": {"name": n, "url": u, "type": t})  # noqa: ANN001,E501

    assert c.install_skill_from_github("https://x")["status"] == "success"
    assert c.install_skill_from_registry("@a/b")["id"] == "@a/b"
    assert c.install_skill_from_file("/tmp/x")["path"] == "/tmp/x"
    assert c.uninstall_skill("old")["status"] == "success"
    assert c.list_installed_skills() == [{"name": "x"}]
    assert c.get_built_in_skills() == [{"name": "b"}]
    assert c.install_built_in_skill("b")["name"] == "b"
    assert c.install_all_built_in_skills()["status"] == "success"
    assert c.get_relevant_skills("ctx", top_k=2)[0]["top_k"] == 2
    assert c.get_system_prompt_with_skills() == "PROMPT"
    assert c.load_skill_with_tool("ok")["status"] == "success"
    assert c.load_skill_with_tool("bad")["status"] == "error"
    assert c.add_skill_registry("r1", "https://repo")["status"] == "success"
    assert c.check_skill_updates()[0]["update"] is True
    assert c.update_skill("x")["name"] == "x"


def test_github_marketplace_feature_gates_and_success_paths(tmp_path: Path) -> None:
    c = _core(tmp_path)
    c.config["settings"]["memory"]["skills_system"] = {
        "github_enabled": False,
        "marketplace_enabled": False,
        "auto_install_enabled": False,
        "sync_on_boot": False,
    }
    c.github_discovery = SimpleNamespace(
        search_skills=lambda **kw: [{"repository": "o/r", "path": "skills/abc"}],  # noqa: ANN001
        get_trending_skills=lambda days, limit: [{"days": days, "limit": limit}],  # noqa: ANN001
        get_skill_recommendations=lambda context, limit: [{"context": context, "limit": limit}],  # noqa: ANN001
        get_skill_catalog=lambda org: {"status": "success", "org": org, "skills": []},  # noqa: ANN001
    )
    c.skill_search_index = SimpleNamespace(
        update_index=lambda skills: len(skills),  # noqa: ANN001
        search=lambda **kw: [{"q": kw.get("query")}],  # noqa: ANN001
        get_categories=lambda: ["cat"],
        get_tags=lambda: ["tag"],
        suggest=lambda prefix, field="name", limit=5: [f"{prefix}-1"],  # noqa: ANN001
        get_index_stats=lambda: {"count": 1},
        clear_index=lambda: None,
    )
    c.skill_installer = SimpleNamespace(install_from_github=lambda url: {"status": "success", "url": url})  # noqa: ANN001,E501

    assert c.search_github_skills(query="x") == []
    assert c.get_trending_skills() == []
    assert c.get_skill_recommendations("x") == []
    assert c.get_github_skill_catalog()["status"] == "disabled"
    assert c.install_skill_from_github_search("abc")["status"] == "disabled"
    assert c.refresh_skill_index()["status"] == "disabled"

    c.config["settings"]["memory"]["skills_system"].update(
        {"github_enabled": True, "marketplace_enabled": True, "auto_install_enabled": True}
    )
    out = c.install_skill_from_github_search("abc")
    assert out["status"] == "success"
    assert "https://github.com/o/r/tree/main/skills/abc" in out["url"]
    assert c.search_skills_local("abc")[0]["q"] == "abc"
    assert c.get_skill_categories() == ["cat"]
    assert c.get_skill_tags() == ["tag"]
    assert c.suggest_skills("ab") == ["ab-1"]
    assert c.get_trending_skills(days=2)[0]["days"] == 2
    assert c.get_skill_recommendations("ctx", limit=3)[0]["limit"] == 3
    assert c.get_github_skill_catalog("x")["org"] == "x"
    assert c.get_index_stats()["count"] == 1
    assert c.refresh_skill_index()["status"] == "success"


def test_sync_github_skills_paths(tmp_path: Path) -> None:
    c = _core(tmp_path)
    calls: list[str] = []
    c.config["settings"]["memory"]["skills_system"] = {
        "github_enabled": True,
        "marketplace_enabled": True,
        "auto_install_enabled": True,
        "sync_on_boot": False,
    }
    c.github_discovery = SimpleNamespace(search_skills=lambda limit=100: [{"x": 1}])  # noqa: ANN001
    c.skill_search_index = SimpleNamespace(update_index=lambda skills: calls.append(f"upd:{len(skills)}"))  # noqa: ANN001,E501
    c._sync_github_skills()
    assert calls == ["upd:1"]

    c.github_discovery = SimpleNamespace(search_skills=lambda limit=100: (_ for _ in ()).throw(RuntimeError("x")))  # noqa: ANN001,E501
    c._sync_github_skills()  # should swallow and continue

    c.config["settings"]["memory"]["skills_system"]["sync_on_boot"] = False
    c._sync_github_skills_async()  # no thread
    c.config["settings"]["memory"]["skills_system"]["sync_on_boot"] = True
    c.config["settings"]["memory"]["skills_system"]["github_enabled"] = False
    c._sync_github_skills_async()  # gate disabled path


def test_remember_and_improvement_llm_wrappers(tmp_path: Path) -> None:
    c = _core(tmp_path)
    c.self_improvement = SimpleNamespace(
        remember=lambda *a, **k: {"status": "success", "id": "r1"},  # noqa: ANN001
        get_improvement_history=lambda limit=10, improvement_type=None, status=None: [{"id": "h1"}],  # noqa: ANN001,E501
        get_metrics=lambda: {"count": 1},
        add_test_case=lambda *a, **k: {"status": "success"},  # noqa: ANN001
        run_validation_suite=lambda: {"status": "success"},
        toggle_llm=lambda enabled: {"status": "success", "enabled": enabled},  # noqa: ANN001
        get_llm_stats=lambda: {"calls": 2},
        get_improvement_summary=lambda limit=10: {"status": "success", "limit": limit},  # noqa: ANN001
        llm=SimpleNamespace(get_model_info=lambda: {"name": "m"}),
        history=[],
    )
    c.llm_enabled = True
    c._safe_text_for_memory_md = lambda text: {"allowed": True, "text": text, "route": "accepted"}  # type: ignore[method-assign]  # noqa: E501
    assert c.remember("keep this")["status"] == "success"

    c._safe_text_for_memory_md = lambda text: {"allowed": False, "text": text, "route": "blocked", "reasons": ["x"]}  # type: ignore[method-assign]  # noqa: E501
    assert c.remember("blocked")["status"] == "blocked"
    c._safe_text_for_memory_md = lambda text: {"allowed": True, "text": text} if text == "ok" else {"allowed": False, "reasons": ["bad"]}  # type: ignore[method-assign]  # noqa: E501
    assert c.remember("ok", content="bad")["status"] == "blocked"

    assert c.get_improvement_history() == [{"id": "h1"}]
    assert c.get_self_improvement_metrics()["count"] == 1
    assert c.add_validation_test("memory", "n", {}, None)["status"] == "success"
    assert c.run_validation_suite()["status"] == "success"
    assert c.toggle_llm(True)["enabled"] is True
    assert c.get_llm_stats()["calls"] == 2
    assert c.get_improvement_summary(limit=7)["limit"] == 7
    assert c.get_model_info()["name"] == "m"

    c.self_improvement = SimpleNamespace(history=[])
    assert c.toggle_llm(False)["status"] == "error"
    assert c.get_llm_stats()["status"] == "error"
    assert c.get_improvement_summary()["status"] == "error"
    c.llm_enabled = False
    assert c.get_model_info()["enabled"] is False
