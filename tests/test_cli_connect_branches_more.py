from __future__ import annotations

import json
from pathlib import Path

from ms8 import cli


def _set_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / "ms8_home"))
    monkeypatch.setenv("MS8_SHORTCUT_AUTO", "0")
    monkeypatch.setenv("OPENCLAW_MEMORY_SESSION_INGEST_ENABLED", "0")


def _last_json_payload(raw: str) -> dict:
    lines = [line for line in raw.splitlines() if line.strip()]
    for idx in range(len(lines)):
        candidate = "\n".join(lines[idx:])
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise AssertionError(f"no json payload found in output: {raw!r}")


def test_connect_run_branch(monkeypatch, tmp_path: Path, capsys) -> None:
    _set_env(monkeypatch, tmp_path)
    from ms8.connect.scripts import connect as connect_mod

    monkeypatch.setattr(connect_mod, "run_connect_flow", lambda target="all": {"result": {"overall_ok": True}})
    rc = cli.main(["connect", "run", "--target", "all"])
    out = capsys.readouterr().out
    assert rc == 0
    payload = _last_json_payload(out)
    assert payload["result"]["overall_ok"] is True


def test_connect_auto_branch(monkeypatch, tmp_path: Path, capsys) -> None:
    _set_env(monkeypatch, tmp_path)
    from ms8.connect.scripts import bootstrap as bootstrap_mod

    calls: list[dict] = []

    def _fake_bootstrap(**kwargs):
        calls.append(dict(kwargs))
        return {"ok": True, "hint": "ok", "self_heal": {}}

    monkeypatch.setattr(bootstrap_mod, "run_bootstrap", _fake_bootstrap)
    rc = cli.main(["connect", "auto", "--target", "claude_desktop", "--max-retries", "2", "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0
    payload = _last_json_payload(out)
    assert payload["ok"] is True
    assert payload["attempt_count"] == 1
    assert calls
    assert calls[-1]["target"] == "claude_desktop"
    assert calls[-1]["dry_run"] is True


def test_connect_list_targets_compact_branch(monkeypatch, tmp_path: Path, capsys) -> None:
    _set_env(monkeypatch, tmp_path)
    from ms8.connect.scripts import client_config as cfg_mod

    monkeypatch.setattr(
        cfg_mod,
        "supported_target_matrix",
        lambda: {
            "x": {"discovery": {"resolved": "/tmp/x.json", "resolved_exists": True}},
        },
    )
    rc = cli.main(["connect", "list-targets", "--compact"])
    out = capsys.readouterr().out
    assert rc == 0
    payload = _last_json_payload(out)
    assert payload["view"] == "compact"
    assert payload["targets"]["x"]["exists"] is True


def test_connect_template_custom_client_branch(monkeypatch, tmp_path: Path, capsys) -> None:
    _set_env(monkeypatch, tmp_path)
    from ms8.connect.scripts import client_config as cfg_mod
    from ms8.connect.scripts import common as common_mod

    runtime_root = tmp_path / "connect_runtime"
    monkeypatch.setattr(common_mod, "connect_root", lambda: runtime_root)
    monkeypatch.setattr(cfg_mod, "payload_for_target", lambda _target: {"mcpServers": {"ms8-memory": {}}})
    rc = cli.main(["connect", "template", "--client-name", "Cherry Studio"])
    out = capsys.readouterr().out
    assert rc == 0
    payload = _last_json_payload(out)
    assert payload["ok"] is True
    assert payload["custom_template_generated"] is True
    assert Path(payload["output"]).exists()


def test_connect_guide_manual_and_agent_branches(monkeypatch, tmp_path: Path, capsys) -> None:
    _set_env(monkeypatch, tmp_path)
    rc = cli.main(["connect", "guide", "--mode", "manual"])
    out_manual = capsys.readouterr().out
    assert rc == 0
    manual_payload = _last_json_payload(out_manual)
    assert manual_payload["mode"] == "manual"
    assert len(manual_payload["steps"]) >= 3

    rc = cli.main(["connect", "guide", "--mode", "agent"])
    out_agent = capsys.readouterr().out
    assert rc == 0
    agent_payload = _last_json_payload(out_agent)
    assert agent_payload["mode"] == "agent"
    assert "bootstrap" in " ".join(agent_payload["steps"])
