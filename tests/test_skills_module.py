from __future__ import annotations

import json
from pathlib import Path

from ms8.engine_core import skills as mod


def _cfg(tmp_path: Path) -> dict:
    mem = tmp_path / "memory"
    return {"memory_dir": mem, "settings": {"memory": {"skills_system": {}}}}


def test_skill_load_and_to_dict(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skillx"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: alpha skill\nversion: 1.2.3\ntriggers: [hello]\n---\n\nbody",
        encoding="utf-8",
    )
    (skill_dir / "resources").mkdir()
    (skill_dir / "resources" / "a.txt").write_text("A", encoding="utf-8")

    s = mod.Skill("alpha", "desc", "project")
    s.path = skill_dir
    assert s.load() is True
    data = s.to_dict()
    assert data["name"] == "alpha"
    assert s.content["description"] == "alpha skill"
    assert s.content["resources"][0]["name"] == "a.txt"


def test_skill_load_invalid_yaml_returns_false(tmp_path: Path, capsys) -> None:
    skill_dir = tmp_path / "bad"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: [\n---\nbody", encoding="utf-8")
    s = mod.Skill("bad", "desc")
    s.path = skill_dir
    assert s.load() is False
    assert "Error parsing skill YAML" in capsys.readouterr().out


def test_skill_manager_create_list_load_delete(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(mod, "get_config", lambda: _cfg(tmp_path))
    mgr = mod.SkillManager()
    created = mgr.create_skill(
        name="my-skill",
        description="desc",
        instructions="do things",
        metadata={"tags": ["a", "A", " "], "triggers": ["x", "X"], "tools": ["read", "read"]},
        resources={"example.md": "hello"},
    )
    assert created["status"] == "success"
    skills = mgr.list_skills()
    assert any(s["name"] == "my-skill" for s in skills)
    full = mgr.load_skill("my-skill")
    assert full is not None and "# Skill: my-skill" in full

    deleted = mgr.delete_skill("my-skill")
    assert deleted["status"] == "success"
    assert mgr.get_skill("my-skill") is None


def test_skill_manager_create_conflict_and_invalid_scope(tmp_path: Path, monkeypatch) -> None:
    cfg = _cfg(tmp_path)
    cfg["settings"]["memory"]["skills_system"] = {"allow_overwrite": False, "auto_suffix_on_conflict": True}
    monkeypatch.setattr(mod, "get_config", lambda: cfg)
    mgr = mod.SkillManager()
    assert mgr.create_skill("dup", "d", "i")["status"] == "success"
    second = mgr.create_skill("dup", "d", "i")
    assert second["status"] == "success"
    assert second["skill"]["name"].startswith("dup-")

    bad = mgr.create_skill("x", "d", "i", scope="invalid")
    assert bad["status"] == "error"


def test_learn_skill_from_trajectory_and_extract_helpers(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(mod, "get_config", lambda: _cfg(tmp_path))
    mgr = mod.SkillManager()
    trajectory = [
        {"role": "user", "content": "please refactor python code and run test", "tool_calls": [{"name": "rg"}]},
        {"role": "assistant", "content": "fixed error on macOS", "tool_calls": [{"name": "pytest"}]},
    ]
    out = mgr.learn_skill_from_trajectory(trajectory, "learned")
    assert out["status"] == "success"
    learned = mgr.get_skill("learned")
    assert learned is not None
    loaded = mgr.load_skill("learned") or ""
    assert "Workflow" in loaded

    # low-length trajectory is rejected
    fail = mgr.learn_skill_from_trajectory([{"role": "user", "content": "only one"}], "too-short")
    assert fail["status"] == "error"


def test_extract_yaml_value_and_trigger_helpers(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(mod, "get_config", lambda: _cfg(tmp_path))
    mgr = mod.SkillManager()
    assert mgr._extract_yaml_value("name: abc\ndescription: d", "name") == "abc"
    assert mgr._extract_yaml_value("x: y", "name") is None

    # ensure dedupe preserve order
    assert mgr._dedupe_preserve_order([" A ", "a", "", "B"]) == ["A", "B"]

    # category inference edges
    assert mgr._derive_category(["sql"], [], []) == "database"
    assert mgr._derive_category(["test"], [], []) == "testing"
    assert mgr._derive_category(["deploy"], [], []) == "deployment"
    assert mgr._derive_category(["python"], [], []) == "code"
    assert mgr._derive_category(["misc"], [], []) == "workflow"

    # tools derivation
    tools = mgr._derive_tools([{"tool_calls": [{"name": "a"}, {"tool": "b"}]}])
    assert tools == ["a", "b"]

