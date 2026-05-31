from __future__ import annotations

from pathlib import Path

import pytest

from ms8.connect.scripts import client_config


def test_external_profiles_invalid_yaml_and_valid_yaml(monkeypatch, tmp_path: Path) -> None:
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir(parents=True, exist_ok=True)
    (profiles_dir / "bad.yaml").write_text(":\n", encoding="utf-8")
    (profiles_dir / "good.yaml").write_text(
        """
name: custom_target
aliases: [custom, ct]
path: ~/.ms8/custom_mcp.json
snippet_file: custom_mcp.json
config_format: json
merge_strategy: upsert
verify_keys: [command, args]
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(client_config, "_profile_dirs", lambda: [profiles_dir])
    loaded = client_config._load_external_profiles()
    assert "custom_target" in loaded
    assert "bad" not in loaded


def test_normalize_target_and_invalid_target() -> None:
    assert client_config.normalize_target("claude") == "claude_desktop"
    assert client_config.normalize_target("all") == "all"
    with pytest.raises(ValueError):
        client_config.normalize_target("not-a-target")


def test_target_paths_with_path_resolver(monkeypatch, tmp_path: Path) -> None:
    fake = tmp_path / "fake_cline.json"
    monkeypatch.setattr(
        client_config,
        "_agent_profiles",
        lambda: {
            "cline": {
                "aliases": ("cline",),
                "path_resolver": (lambda: fake),
                "snippet_file": "cline_mcp.json",
                "config_format": "json",
                "merge_strategy": "upsert",
                "verify_keys": ("command", "args"),
            }
        },
    )
    paths = client_config.target_paths("cline")
    assert paths["cline"] == fake


def test_target_discovery_for_codable_targets(monkeypatch, tmp_path: Path) -> None:
    cline = tmp_path / "cline_mcp.json"
    codex = tmp_path / "codex.toml"
    monkeypatch.setattr(client_config, "_cline_candidates", lambda: [cline])
    monkeypatch.setattr(client_config, "_resolve_cline_path", lambda: cline)
    monkeypatch.setattr(client_config, "_codex_candidates", lambda: [codex])
    monkeypatch.setattr(client_config, "_resolve_codex_path", lambda: codex)
    data = client_config.target_discovery("all")
    assert "cline" in data
    assert data["cline"]["strategy"] == "candidate_scan_then_fallback"
    assert "codex" in data
    assert data["codex"]["config_format"] == "toml"


def test_payload_for_target_json_and_toml(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("MS8_MCP_COMMAND", raising=False)
    monkeypatch.setattr(client_config.shutil, "which", lambda *_: None)
    payload_json = client_config.payload_for_target("claude_desktop")
    assert "mcpServers" in payload_json
    assert client_config.SERVER_NAME in payload_json["mcpServers"]

    codex = tmp_path / "config.toml"
    monkeypatch.setattr(client_config, "_resolve_codex_path", lambda: codex)
    payload_toml = client_config.payload_for_target("codex")
    assert "mcp_servers" in payload_toml
    assert client_config.SERVER_NAME in payload_toml["mcp_servers"]


def test_expected_command_signature_override(monkeypatch) -> None:
    monkeypatch.setenv("MS8_MCP_COMMAND", "python3")
    monkeypatch.setenv("MS8_MCP_COMMAND_PREFIX_ARGS", "-m ms8.connect.mcp_server.stdio_server")
    cmd, prefix = client_config.expected_command_signature("claude_desktop")
    assert cmd == "python3"
    assert prefix == ("-m", "ms8.connect.mcp_server.stdio_server")
