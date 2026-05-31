from __future__ import annotations

import json
from pathlib import Path

from ms8.engine_core.agent_skills_standard import AgentSkill, SkillCategory, SkillMetadata, SkillTest


def test_skill_metadata_roundtrip_dict_and_yaml() -> None:
    meta = SkillMetadata(
        name="skill-a",
        description="desc",
        category=SkillCategory.SECURITY,
        triggers=["remember", "/a.+b/"],
        tools=["search"],
    )
    data = meta.to_dict()
    assert data["category"] == "security"
    restored = SkillMetadata.from_dict(data)
    assert restored.name == "skill-a"
    assert restored.category == "security"

    yaml_text = "name: y\n" "description: z\n" "category: other\n"
    loaded = SkillMetadata.from_yaml(yaml_text)
    assert loaded.name == "y"
    assert loaded.description == "z"


def _write_skill_tree(base: Path) -> Path:
    skill_dir = base / "my-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: my_skill\n"
        "description: does things\n"
        "triggers:\n"
        "  - remember\n"
        "  - /r[0-9]+/\n"
        "---\n\n"
        "body content\n",
        encoding="utf-8",
    )
    (skill_dir / "resources").mkdir()
    (skill_dir / "resources" / "example.py").write_text("print('x')\n", encoding="utf-8")
    (skill_dir / "resources" / "readme.md").write_text("notes\n", encoding="utf-8")
    (skill_dir / "tests").mkdir()
    (skill_dir / "tests" / "tests.json").write_text(
        json.dumps({"tests": [{"name": "t1", "input": "in", "expected_output_contains": ["ok"]}]}),
        encoding="utf-8",
    )
    (skill_dir / "examples").mkdir()
    (skill_dir / "examples" / "sample.md").write_text("hello", encoding="utf-8")
    return skill_dir


def test_agent_skill_load_from_markdown_tree(tmp_path: Path) -> None:
    skill_dir = _write_skill_tree(tmp_path)
    skill = AgentSkill(skill_dir)
    assert skill.load() is True
    assert skill.loaded is True
    assert skill.metadata is not None
    assert skill.metadata.name == "my_skill"
    assert skill.content == "body content"
    assert len(skill.resources) == 2
    assert len(skill.tests) == 1
    assert len(skill.examples) == 1
    assert skill.matches_trigger("please remember this")
    assert skill.matches_trigger("r123")
    assert not skill.matches_trigger("no-match")
    full = skill.get_full_content()
    assert "## Resources" in full
    assert "## Examples" in full


def test_agent_skill_load_from_json_and_save(tmp_path: Path) -> None:
    skill_dir = tmp_path / "json-skill"
    skill_dir.mkdir()
    (skill_dir / "skill.json").write_text(
        json.dumps(
            {
                "metadata": {"name": "json_skill", "description": "json desc", "triggers": ["foo"]},
                "content": "json body",
            }
        ),
        encoding="utf-8",
    )
    skill = AgentSkill(skill_dir)
    assert skill.load() is True
    assert skill.metadata is not None and skill.metadata.name == "json_skill"
    assert skill.content == "json body"

    # save path with resources/tests
    out_dir = tmp_path / "saved-skill"
    out = AgentSkill(out_dir)
    out.metadata = SkillMetadata(name="saved", description="saved desc")
    out.content = "saved body"
    out.resources = [skill.resources[0]] if skill.resources else []
    out.tests = [SkillTest(name="case", input="hello")]
    assert out.save() is True
    assert (out_dir / "SKILL.md").exists()
    assert (out_dir / "tests" / "tests.json").exists()


def test_agent_skill_invalid_cases_and_regex_warning(tmp_path: Path, capsys) -> None:
    missing = AgentSkill(tmp_path / "no-skill")
    assert missing.load() is False

    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    (bad_dir / "SKILL.md").write_text("---\nname: [\n---\nbody", encoding="utf-8")
    bad = AgentSkill(bad_dir)
    assert bad.load() is False

    regex_dir = tmp_path / "regex"
    regex_dir.mkdir()
    (regex_dir / "SKILL.md").write_text(
        "---\nname: rx\ndescription: rx\ntriggers:\n  - '/[a-/'\n---\ncontent\n",
        encoding="utf-8",
    )
    rx = AgentSkill(regex_dir)
    assert rx.load() is True
    assert rx.matches_trigger("abc") is False
    assert "Invalid trigger regex" in capsys.readouterr().out

