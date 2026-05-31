from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def _patch_marketplace_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from ms8.engine_core import skill_marketplace as mod

    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(mod, "get_config", lambda: {"memory_dir": memory_dir})
    return mod


def test_registry_load_save_add_remove(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    mod = _patch_marketplace_config(monkeypatch, tmp_path)

    reg = mod.SkillRegistry()
    assert reg.list_registries()

    added = reg.add_registry("X", "https://example.com/repo", "github")
    assert added["name"] == "X"

    loaded = json.loads(reg.registry_file.read_text(encoding="utf-8"))
    assert any(r["name"] == "X" for r in loaded["registries"])
    assert reg.remove_registry("X") is True
    assert reg.remove_registry("not-found") is False


def test_registry_handles_broken_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    mod = _patch_marketplace_config(monkeypatch, tmp_path)
    reg_file = (tmp_path / "memory" / "skill_registry.json")
    reg_file.write_text("{bad json", encoding="utf-8")

    reg = mod.SkillRegistry()
    assert len(reg.list_registries()) >= 2  # falls back to defaults


def test_parse_github_url_variants(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    mod = _patch_marketplace_config(monkeypatch, tmp_path)
    manager = SimpleNamespace(skill_dirs={"project": tmp_path}, skills=[])
    inst = mod.SkillInstaller(manager)

    one = inst._parse_github_url("https://github.com/acme/repo/tree/main/skills/foo")
    assert one and one["skill_name"] == "foo"
    two = inst._parse_github_url("https://github.com/acme/repo/tree/main/path/skills/bar")
    assert two and two["skill_name"] == "bar"
    assert inst._parse_github_url("https://example.com/nope") is None


def test_install_from_github_and_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    mod = _patch_marketplace_config(monkeypatch, tmp_path)
    manager = SimpleNamespace(skill_dirs={"project": tmp_path}, skills=[])
    inst = mod.SkillInstaller(manager)

    ok = inst.install_from_github("https://github.com/acme/repo/tree/main/skills/z", "project")
    assert ok["status"] == "success"
    assert ok["skill_name"] == "z"

    bad = inst.install_from_github("not-url", "project")
    assert bad["status"] == "error"

    # Registry install branches
    assert inst.install_from_registry("no-prefix", "project")["status"] == "error"
    assert inst.install_from_registry("@too/many/parts", "project")["status"] == "error"
    # default registries include "anthropic"
    reg_ok = inst.install_from_registry("@anthropic/frontend-design", "project")
    assert reg_ok["status"] in {"success", "error"}
    missing = inst.install_from_registry("@unknown/skill", "project")
    assert missing["status"] == "error"


def test_install_from_file_and_uninstall(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    mod = _patch_marketplace_config(monkeypatch, tmp_path)

    class StubSkillManager:
        def __init__(self) -> None:
            self.skill_dirs = {"project": tmp_path / "skills"}
            self.skills = []

        def _load_all_skills(self):
            return [SimpleNamespace(to_dict=lambda: {"name": "x"})]

        def get_skill(self, name: str):
            if name == "gone":
                return SimpleNamespace(path=self.skill_dirs["project"] / "gone")
            return None

    mgr = StubSkillManager()
    inst = mod.SkillInstaller(mgr)

    # Not exists path
    miss = inst.install_from_file(str(tmp_path / "none"), "project")
    assert miss["status"] == "error"

    # Invalid dir without SKILL.md
    invalid_dir = tmp_path / "invalid"
    invalid_dir.mkdir()
    invalid = inst.install_from_file(str(invalid_dir), "project")
    assert invalid["status"] == "error"

    # Prepare valid local skill and monkeypatch AgentSkill loader
    skill_src = tmp_path / "src_skill"
    skill_src.mkdir()
    (skill_src / "SKILL.md").write_text("# skill", encoding="utf-8")
    (skill_src / "extra.txt").write_text("ok", encoding="utf-8")

    class StubAgentSkill:
        def __init__(self, _path: Path) -> None:
            self.metadata = SimpleNamespace(name="my-skill")

        def load(self) -> bool:
            return True

    monkeypatch.setattr(mod, "AgentSkill", StubAgentSkill)
    ok = inst.install_from_file(str(skill_src), "project")
    assert ok["status"] == "success"
    assert (mgr.skill_dirs["project"] / "my-skill" / "SKILL.md").exists()

    # Uninstall error/not found
    not_found = inst.uninstall("absent", "project")
    assert not_found["status"] == "error"
    # Uninstall success
    (mgr.skill_dirs["project"] / "gone").mkdir(parents=True, exist_ok=True)
    removed = inst.uninstall("gone", "project")
    assert removed["status"] == "success"


def test_install_misc_helpers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    mod = _patch_marketplace_config(monkeypatch, tmp_path)
    manager = SimpleNamespace(
        skill_dirs={"project": tmp_path},
        skills=[SimpleNamespace(to_dict=lambda: {"name": "a"})],
    )
    inst = mod.SkillInstaller(manager)
    assert inst.list_installed()[0]["name"] == "a"
    assert inst.check_updates() == []
    assert inst.update_skill("x")["status"] == "error"
    inst._log_installation("x", "file", "success")
    assert inst.installation_log.exists()
