from __future__ import annotations

import json

from ms8.engine_core import subagents as mod


def _fake_config(tmp_path):
    return {"memory_dir": tmp_path / "memory"}


def test_subagent_to_dict_has_required_fields() -> None:
    agent = mod.SubAgent("demo", "desc", ["read"])
    payload = agent.to_dict()
    assert payload["name"] == "demo"
    assert payload["description"] == "desc"
    assert payload["tools"] == ["read"]
    assert payload["id"]


def test_manager_lists_builtins_and_custom(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(mod, "get_config", lambda: _fake_config(tmp_path))
    custom_file = tmp_path / "memory" / "subagents" / "custom.md"
    custom_file.parent.mkdir(parents=True, exist_ok=True)
    custom_file.write_text(
        "---\nname: custom\ndescription: custom agent\n---\nbody\n",
        encoding="utf-8",
    )

    mgr = mod.SubAgentManager()
    names = {x["name"] for x in mgr.list_subagents()}
    assert {"explore", "memory", "recall", "reflection", "custom"} <= names


def test_spawn_foreground_background_and_not_found(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(mod, "get_config", lambda: _fake_config(tmp_path))
    mgr = mod.SubAgentManager()

    not_found = mgr.spawn("nope", "task")
    assert not_found["status"] == "error"

    fg = mgr.spawn("explore", "topic", background=False)
    assert fg["status"] == "success"
    assert "Analyzed" in fg["result"]
    assert fg["duration_seconds"] >= 0

    bg = mgr.spawn("memory", "cleanup", background=True)
    assert bg["status"] == "launched"
    task_file = tmp_path / "memory" / "subagents" / f"{bg['task_id']}.json"
    assert task_file.exists()
    data = json.loads(task_file.read_text(encoding="utf-8"))
    assert data["status"] == "queued"


def test_create_custom_subagent_writes_markdown(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(mod, "get_config", lambda: _fake_config(tmp_path))
    mgr = mod.SubAgentManager()
    out = mgr.create_custom_subagent("writer", "does writing", "be concise", ["read", "write"])
    assert out["status"] == "success"
    md = tmp_path / "memory" / "subagents" / "writer.md"
    assert md.exists()
    text = md.read_text(encoding="utf-8")
    assert "name: writer" in text
    assert "tools: read, write" in text
    assert any(a.name == "writer" for a in mgr.custom_subagents)

