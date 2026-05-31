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


def test_ops_llm_status_returns_zero_even_when_not_ready(monkeypatch, tmp_path: Path, capsys) -> None:
    _set_env(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "get_engine_llm_status", lambda: {"ok": False, "enabled": False})
    rc = cli.main(["ops", "llm-status"])
    out = capsys.readouterr().out
    assert rc == 0
    payload = _last_json_payload(out)
    assert payload["enabled"] is False


def test_ops_compression_status_renders_lifecycle(monkeypatch, tmp_path: Path, capsys) -> None:
    _set_env(monkeypatch, tmp_path)
    monkeypatch.setattr(
        cli,
        "monitoring_status_runtime",
        lambda: {
            "result": {
                "compression_freshness": {"stale_hours": 1},
                "core_metrics": {"x": 1},
                "maintenance_policy_stats": {"y": 2},
            }
        },
    )
    rc = cli.main(["ops", "compression-status"])
    out = capsys.readouterr().out
    assert rc == 0
    payload = _last_json_payload(out)
    assert payload["status"] == "success"
    assert payload["lifecycle"]["core_metrics"]["x"] == 1


def test_ops_learn_skill_usage_error_when_non_list(monkeypatch, tmp_path: Path, capsys) -> None:
    _set_env(monkeypatch, tmp_path)
    bad = tmp_path / "traj.json"
    bad.write_text('{"not":"list"}', encoding="utf-8")
    rc = cli.main(["ops", "learn-skill", "--trajectory-file", str(bad), "--skill-name", "x"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "must contain a JSON list" in err


def test_ops_learn_skill_success(monkeypatch, tmp_path: Path, capsys) -> None:
    _set_env(monkeypatch, tmp_path)
    good = tmp_path / "traj.json"
    good.write_text('[{"step":"a"}]', encoding="utf-8")
    monkeypatch.setattr(cli, "learn_skill_runtime", lambda **kwargs: {"ok": True, "skill": kwargs["skill_name"]})
    rc = cli.main(["ops", "learn-skill", "--trajectory-file", str(good), "--skill-name", "x"])
    out = capsys.readouterr().out
    assert rc == 0
    payload = _last_json_payload(out)
    assert payload["skill"] == "x"


def test_ops_subagent_spawn_success(monkeypatch, tmp_path: Path, capsys) -> None:
    _set_env(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_read_labs_enabled", lambda: True)
    monkeypatch.setattr(
        cli,
        "spawn_subagent_runtime",
        lambda **kwargs: {"ok": True, "name": kwargs["subagent_name"], "background": kwargs["background"]},
    )
    rc = cli.main(["ops", "subagent-spawn", "--name", "explore", "--task", "check", "--background"])
    out = capsys.readouterr().out
    assert rc == 0
    payload = _last_json_payload(out)
    assert payload["name"] == "explore"
    assert payload["background"] is True


def test_ops_labs_gate_blocks_experimental_cmd(monkeypatch, tmp_path: Path, capsys) -> None:
    _set_env(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_read_labs_enabled", lambda: False)
    rc = cli.main(["ops", "synthetic-generate"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "labs command disabled by default" in err
