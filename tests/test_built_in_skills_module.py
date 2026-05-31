from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


def _patch_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from ms8.engine_core import built_in_skills as mod

    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(mod, "get_config", lambda: {"memory_dir": memory_dir})
    return mod


def test_built_in_skills_install_and_list(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    mod = _patch_config(monkeypatch, tmp_path)
    bis = mod.BuiltInSkills()

    skills = bis.get_built_in_skills()
    assert len(skills) >= 3
    assert all("installed" in row for row in skills)

    ok = bis.install_built_in("python-development")
    assert ok["status"] == "success"
    installed = bis.bundled_skills_dir / "python-development" / "SKILL.md"
    assert installed.exists()

    miss = bis.install_built_in("not-exists")
    assert miss["status"] == "error"


def test_install_all_built_in(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    mod = _patch_config(monkeypatch, tmp_path)
    bis = mod.BuiltInSkills()

    out = bis.install_all_built_in()
    assert out["status"] == "success"
    assert isinstance(out["details"], list)

    # Second run should keep success and usually install fewer/no new skills.
    out2 = bis.install_all_built_in()
    assert out2["status"] == "success"


class _AgentSkill:
    def __init__(self) -> None:
        cat = SimpleNamespace(value="testing")
        self.metadata = SimpleNamespace(
            name="agent-test",
            description="pytest helper",
            tags=["pytest", "unit"],
            triggers=["unit test", r"/coverage/i"],
            category=cat,
        )

    def matches_trigger(self, context: str) -> bool:
        return "unit test" in context.lower()


class _ContentSkill:
    def __init__(self, bad_regex: bool = False) -> None:
        trigger = "/(/" if bad_regex else "/react/i"
        self.name = "content-react"
        self.description = "react helper"
        self.content = {
            "name": "content-react",
            "description": "react helper",
            "frontmatter": f"""
tags:
  - react
triggers:
  - "{trigger}"
category: frontend
""",
        }


def test_skill_discovery_covers_object_dict_and_content_forms(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_config(monkeypatch, tmp_path)
    from ms8.engine_core.built_in_skills import SkillDiscovery

    dict_skill = {
        "metadata": {
            "name": "dict-api",
            "description": "api tool",
            "tags": ["api"],
            "triggers": ["endpoint"],
            "category": "backend",
        }
    }
    manager = SimpleNamespace(skills=[_AgentSkill(), _ContentSkill(), dict_skill, object()])
    sd = SkillDiscovery(manager)
    items = sd.get_relevant_skills("Please write unit test coverage for endpoint api", top_k=5)
    assert items
    assert any(x["skill"]["metadata"]["name"] == "agent-test" for x in items)
    assert any(x["skill"]["metadata"]["name"] == "dict-api" for x in items)


def test_skill_discovery_handles_invalid_regex(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_config(monkeypatch, tmp_path)
    from ms8.engine_core.built_in_skills import SkillDiscovery

    manager = SimpleNamespace(skills=[_ContentSkill(bad_regex=True)])
    sd = SkillDiscovery(manager)
    items = sd.get_relevant_skills("react ui", top_k=3)
    assert isinstance(items, list)


def test_system_prompt_injection(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_config(monkeypatch, tmp_path)
    from ms8.engine_core.built_in_skills import SkillDiscovery

    empty_manager = SimpleNamespace(list_skills=lambda: [])
    sd_empty = SkillDiscovery(empty_manager)
    assert sd_empty.get_system_prompt_injection() == ""

    many = [{"metadata": {"name": f"s{i}", "description": "d"}} for i in range(12)]
    manager = SimpleNamespace(list_skills=lambda: many)
    sd = SkillDiscovery(manager)
    text = sd.get_system_prompt_injection()
    assert "Available Skills" in text
    assert "more skills" in text
