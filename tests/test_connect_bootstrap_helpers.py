from __future__ import annotations

import json
from pathlib import Path

from ms8.connect.scripts import bootstrap


def test_build_actionable_hints_and_shortest_chain() -> None:
    profiles = {
        "claude_desktop": {"status": "degraded", "activation": {"activation_detected": False}},
        "cursor": {"status": "manual", "activation": {"activation_detected": True}},
        "generic_json": {"status": "manual"},
        "openclaw": {"status": "ready"},
    }
    hints = bootstrap._build_actionable_hints(profiles, target="all")
    assert any("claude_desktop" in h for h in hints)
    assert any("cursor" in h for h in hints)
    assert any("generic_json" in h for h in hints)

    chain = bootstrap._build_shortest_repair_chain(profiles)
    assert "ms8 connect apply --target claude_desktop" in chain
    assert "ms8 connect verify --target cursor" in chain


def test_build_actionable_hints_all_ready() -> None:
    hints = bootstrap._build_actionable_hints({"a": {"status": "ready"}}, target="all")
    assert len(hints) == 1
    assert "All discovered targets are ready" in hints[0]


def test_target_exists_path_resolution(monkeypatch, tmp_path: Path) -> None:
    target_file = tmp_path / "x.json"
    target_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(bootstrap, "target_paths", lambda _t: {"cursor": target_file})
    assert bootstrap._target_exists("cursor") is True

    monkeypatch.setattr(bootstrap, "target_paths", lambda _t: {"cursor": tmp_path / "missing.json"})
    assert bootstrap._target_exists("cursor") is False

    def _boom(_t):
        raise ValueError("bad")

    monkeypatch.setattr(bootstrap, "target_paths", _boom)
    assert bootstrap._target_exists("cursor") is False


def test_write_first_install_report_outputs_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(bootstrap, "connect_root", lambda: tmp_path)
    payload = {
        "generated_at": "now",
        "target": "all",
        "counts": {"ready": 1, "degraded": 1, "manual": 1},
        "profiles": {"cursor": {"status": "degraded", "path": "/tmp/x", "guidance": "run apply"}},
        "one_time_hint": "hint",
        "actionable_hints": ["h1"],
        "shortest_repair_chain": "cmd",
    }
    out = bootstrap._write_first_install_report(payload)
    assert Path(out["json_path"]).exists()
    assert Path(out["text_path"]).exists()
    txt = Path(out["text_path"]).read_text(encoding="utf-8")
    assert "MS8 First-Install Connect Report" in txt
    assert "one_time_hint: hint" in txt
    assert "shortest_repair_chain: cmd" in txt


def test_repair_target_configs_json_branch(tmp_path: Path, monkeypatch) -> None:
    cfg = tmp_path / "cursor.json"
    cfg.write_text(json.dumps({"mcpServers": {"ms8-memory": {"command": "old", "args": []}}}), encoding="utf-8")
    details = {
        "cursor": {
            "path": str(cfg),
            "command_ok": False,
            "args_ok": False,
            "has_ms8_server": True,
        }
    }
    monkeypatch.setattr(bootstrap, "target_profile", lambda _n: {"config_format": "json"})
    monkeypatch.setattr(bootstrap, "expected_route_args", lambda _t: ("--target", "cursor"))
    out = bootstrap._repair_target_configs("cursor", {"details": details})
    assert out["ok"] is True
    after = json.loads(cfg.read_text(encoding="utf-8"))
    server = after["mcpServers"]["ms8-memory"]
    assert server["command"]
    assert "--target" in server["args"]
    assert server["env"]["MS8_AGENT_TARGET"] == "cursor"


def test_repair_target_configs_toml_branch(tmp_path: Path, monkeypatch) -> None:
    cfg = tmp_path / "continue.toml"
    cfg.write_text("", encoding="utf-8")
    details = {
        "continue": {
            "path": str(cfg),
            "command_ok": False,
            "args_ok": False,
            "has_ms8_server": False,
        }
    }
    monkeypatch.setattr(bootstrap, "target_profile", lambda _n: {"config_format": "toml"})
    monkeypatch.setattr(bootstrap, "expected_route_args", lambda _t: ("--target", "continue"))
    out = bootstrap._repair_target_configs("continue", {"details": details})
    assert out["ok"] is True
    txt = cfg.read_text(encoding="utf-8")
    assert "[mcp_servers.ms8-memory]" in txt
    assert "MS8_AGENT_TARGET" in txt
