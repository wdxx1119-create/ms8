from __future__ import annotations

import json
from pathlib import Path

from ms8.connect.adapter_registry import scan_tools
from ms8.connect.scripts import status, verify_client_configs as verify


def test_scan_tools_available_map(monkeypatch) -> None:
    mapping = {"python3": "/tmp/python3", "ollama": "", "git": "/tmp/git"}
    monkeypatch.setattr(scan_tools.shutil, "which", lambda name: mapping.get(name, ""))
    monkeypatch.setattr(scan_tools, "_path_exists", lambda p: p in {"/tmp/python3", "/tmp/git"})
    out = scan_tools.scan_local_tools()
    assert out["ok"] is True
    assert out["available"]["python"] is True
    assert out["available"]["ollama"] is False
    assert out["available"]["git"] is True


def test_status_helpers_and_main(monkeypatch, tmp_path: Path) -> None:
    audit = tmp_path / "logs" / "audit.log"
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit.write_text("1\n2\n3\n", encoding="utf-8")
    assert status._tail_steps(audit, 2) == ["2", "3"]
    assert status._tail_steps(tmp_path / "missing.log", 2) == []

    monkeypatch.setattr(status, "selected_targets", lambda _t: ["claude_desktop"])
    monkeypatch.setattr(status, "target_discovery", lambda _t: {"claude_desktop": {"ok": True}})
    monkeypatch.setattr(status, "target_paths", lambda _name: {"claude_desktop": tmp_path / "cfg.json"})
    profile = status._target_connectivity_status("all")
    assert profile["claude_desktop"]["exists"] is False
    assert profile["claude_desktop"]["discovery"]["ok"] is True

    class _Svc:
        def status(self):
            return {"ok": True, "svc": "up"}

    class _Factory:
        @staticmethod
        def from_config(_cfg):
            return _Svc()

    monkeypatch.setattr(status, "MemoryServiceInterface", _Factory)
    monkeypatch.setattr(status, "load_yaml", lambda _p: {"x": 1})
    monkeypatch.setattr(status, "connect_package_root", lambda: tmp_path)
    monkeypatch.setattr(status, "connect_root", lambda: tmp_path)
    out = status.main("all")
    assert out["ok"] is True
    assert "audit_tail" in out
    assert "target_profiles" in out


def test_verify_command_match_and_readers(tmp_path: Path) -> None:
    assert verify._command_matches("/usr/bin/python3.14", "/opt/homebrew/bin/python3") is True
    assert verify._command_matches("/bin/bash", "/usr/bin/python3") is False
    assert verify._command_matches("", "python3") is False

    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{", encoding="utf-8")
    assert verify._read_json(bad_json) == {}

    bad_toml = tmp_path / "bad.toml"
    bad_toml.write_text("[a", encoding="utf-8")
    assert verify._read_toml(bad_toml) == {}


def test_verify_run_json_and_toml_targets(monkeypatch, tmp_path: Path) -> None:
    json_cfg = tmp_path / "claude.json"
    json_payload = {
        "mcpServers": {
            "ms8-memory": {
                "command": "/usr/bin/python3",
                "args": ["-m", "src.ms8.connect.mcp_server.stdio_server"],
                "env": {"MS8_HOME": "/tmp/ms8"},
            }
        }
    }
    json_cfg.write_text(json.dumps(json_payload, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(verify, "target_paths", lambda _target: {"claude_desktop": json_cfg})
    monkeypatch.setattr(verify, "expected_command_signature", lambda _k: ("/opt/homebrew/bin/python3", ("-m",)))
    monkeypatch.setattr(verify, "expected_route_args", lambda _k: ("src.ms8.connect.mcp_server.stdio_server",))
    monkeypatch.setattr(
        verify,
        "target_profile",
        lambda _k: {"config_format": "json", "verify_keys": ["command", "args", "env.MS8_HOME"]},
    )
    out = verify.run("claude_desktop")
    assert out["ok"] is True
    detail = out["details"]["claude_desktop"]
    assert detail["command_ok"] is True
    assert detail["args_ok"] is True
    assert detail["verify_keys_ok"] is True

    toml_cfg = tmp_path / "continue.toml"
    toml_cfg.write_text(
        '\n'.join(
            [
                "[mcp_servers.ms8-memory]",
                'command = "python3"',
                'args = ["-m", "src.ms8.connect.mcp_server.stdio_server"]',
                "[mcp_servers.ms8-memory.env]",
                'MS8_HOME = "/tmp/ms8"',
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(verify, "target_paths", lambda _target: {"continue": toml_cfg})
    monkeypatch.setattr(verify, "expected_command_signature", lambda _k: ("python3", ("-m",)))
    monkeypatch.setattr(verify, "expected_route_args", lambda _k: ("src.ms8.connect.mcp_server.stdio_server",))
    monkeypatch.setattr(
        verify,
        "target_profile",
        lambda _k: {"config_format": "toml", "verify_keys": ["command", "args", "env.MS8_HOME"]},
    )
    out2 = verify.run("continue")
    assert out2["ok"] is True
    detail2 = out2["details"]["continue"]
    assert detail2["has_ms8_server"] is True
