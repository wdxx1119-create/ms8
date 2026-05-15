from __future__ import annotations

import json
from pathlib import Path

from ms8.connect.scripts.apply_client_configs import run as apply_client_configs
from ms8.connect.scripts.client_config import (
    expected_command_signature,
    expected_route_args,
    normalize_target,
    selected_targets,
    snippet_paths,
    supported_target_matrix,
    target_discovery,
    target_paths,
)
from ms8.connect.scripts.connect import _target_readiness
from ms8.connect.scripts.generate_client_configs import run as generate_client_configs
from ms8.connect.scripts.smoke_test import run_smoke_test
from ms8.connect.scripts.status import main as connect_status_main
from ms8.connect.scripts.verify_client_configs import run as verify_client_configs


def test_target_alias_normalization():
    assert normalize_target("claude") == "claude_desktop"
    assert normalize_target("open_claw") == "openclaw"
    assert normalize_target("hermes_agent") == "hermes"
    assert normalize_target("cherry") == "cherry_studio"
    assert normalize_target("roo_code") == "roo"
    assert normalize_target("generic") == "generic_json"
    assert normalize_target("codex_desktop") == "codex"


def test_selected_targets_all_contains_new_profiles():
    names = selected_targets("all")
    assert "openclaw" in names
    assert "hermes" in names
    assert "cursor" in names
    assert "cline" in names
    assert "roo" in names
    assert "continue" in names
    assert "cherry_studio" in names
    assert "codex" in names
    assert "generic_json" in names


def test_generate_specific_target(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    monkeypatch.setenv("OPENCLAW_MEMORY_AUTO_ROOT", str(tmp_path / ".ms8_runtime" / "connect"))
    out = generate_client_configs(target="hermes")
    assert out["ok"] is True
    files = out["files"]
    assert len(files) == 1
    assert files[0].endswith("hermes_mcp.json")
    snippet_file = tmp_path / ".ms8_runtime" / "connect" / "runtime" / "client_snippets" / "hermes_mcp.json"
    assert snippet_file.exists()
    payload = json.loads(snippet_file.read_text(encoding="utf-8"))
    server = payload["mcpServers"]["ms8-memory"]
    cmd, prefix = expected_command_signature("hermes")
    assert server["command"] == cmd
    assert server["args"] == [*prefix, *expected_route_args("hermes")]
    assert server["env"]["MS8_AGENT_TARGET"] == "hermes"


def test_target_paths_specific():
    mapping = target_paths("openclaw")
    assert list(mapping.keys()) == ["openclaw"]
    assert str(mapping["openclaw"]).endswith(".openclaw/mcp.json")
    snippets = snippet_paths("openclaw")
    assert snippets["openclaw"] == "openclaw_mcp.json"


def test_cherry_discovery_reports_candidates():
    out = target_discovery("cherry_studio")
    info = out["cherry_studio"]
    assert info["strategy"] == "candidate_scan_then_fallback"
    assert isinstance(info["candidates"], list)
    assert len(info["candidates"]) >= 2


def test_supported_target_matrix_contains_policy_fields():
    out = supported_target_matrix()
    assert "cline" in out
    assert "generic_json" in out
    assert out["generic_json"]["merge_strategy"] == "replace"
    assert "verify_keys" in out["openclaw"]


def test_smoke_contains_target_metadata(monkeypatch):
    class _Svc:
        def submit(self, *_args, **_kwargs):
            return {"ok": True}

        def query(self, *_args, **_kwargs):
            return {"ok": True}

        def context(self, *_args, **_kwargs):
            return {"ok": True}

        def status(self, *_args, **_kwargs):
            return {"ok": True}

    monkeypatch.setattr(
        "ms8.connect.scripts.smoke_test.MemoryServiceInterface.from_config",
        lambda *_args, **_kwargs: _Svc(),
    )
    out = run_smoke_test(target="hermes")
    assert out["ok"] is True
    assert out["target"] == "hermes"
    assert out["target_profiles"] == ["hermes"]


def test_status_contains_target_profiles(monkeypatch):
    monkeypatch.setattr("ms8.connect.scripts.status.run_status", lambda: {"ok": True})
    out = connect_status_main(target="openclaw")
    assert out["ok"] is True
    assert out["target"] == "openclaw"
    assert "openclaw" in out["target_profiles"]


def test_target_readiness_states():
    verify = {
        "details": {
            "openclaw": {
                "path": "/tmp/openclaw.json",
                "exists": True,
                "has_ms8_server": True,
                "command_ok": False,
                "args_ok": True,
                "legacy_path_found": False,
            },
            "hermes": {
                "path": "/tmp/hermes.json",
                "exists": False,
                "has_ms8_server": False,
                "command_ok": False,
                "args_ok": False,
                "legacy_path_found": False,
            },
            "generic_json": {
                "path": "/tmp/generic.json",
                "exists": True,
                "has_ms8_server": True,
                "command_ok": True,
                "args_ok": True,
                "legacy_path_found": False,
            },
        }
    }
    out = _target_readiness(verify, "all")
    assert out["profiles"]["openclaw"]["status"] == "degraded"
    assert out["profiles"]["hermes"]["status"] == "manual"
    assert out["profiles"]["generic_json"]["status"] == "ready"
    assert out["profiles"]["generic_json"]["kind"] == "export"


def test_apply_then_verify_generic_json(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OPENCLAW_MEMORY_AUTO_ROOT", str(tmp_path / ".ms8_runtime" / "connect"))
    out_gen = generate_client_configs(target="generic_json")
    assert out_gen["ok"] is True
    out_apply = apply_client_configs(target="generic_json")
    assert out_apply["ok"] is True
    out_verify = verify_client_configs(target="generic_json")
    assert out_verify["ok"] is True


def test_apply_cherry_returns_hint(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OPENCLAW_MEMORY_AUTO_ROOT", str(tmp_path / ".ms8_runtime" / "connect"))
    out_gen = generate_client_configs(target="cherry_studio")
    assert out_gen["ok"] is True
    out_apply = apply_client_configs(target="cherry_studio")
    assert out_apply["ok"] is True
    assert any("Cherry Studio:" in h for h in out_apply.get("hints", []))


def test_apply_then_verify_codex_toml(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OPENCLAW_MEMORY_AUTO_ROOT", str(tmp_path / ".ms8_runtime" / "connect"))
    out_gen = generate_client_configs(target="codex")
    assert out_gen["ok"] is True
    assert out_gen["files"][0].endswith("codex_mcp.toml")
    out_apply = apply_client_configs(target="codex")
    assert out_apply["ok"] is True
    out_verify = verify_client_configs(target="codex")
    assert out_verify["ok"] is True
