from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

from ms8.agent_native.agent_cli import run_agent_cli
from ms8.agent_native.onboarding import init_agent_native


def test_task_verify_and_policy_verify_pass(tmp_path: Path, monkeypatch, capsys) -> None:
    home_dir = tmp_path / "home"
    home_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("MS8_HOME", str(tmp_path / "runtime"))
    monkeypatch.chdir(tmp_path)
    init_agent_native("DEFAULT_SAFE", cwd=tmp_path, force=True, dry_run=False, confirm=False)

    c1 = run_agent_cli(Namespace(agent_cmd="task", task_cmd="verify"))
    assert c1 == 0
    out1 = capsys.readouterr().out
    assert "MS8_AGENT_TASK_VERIFY" in out1
    assert "status=PASS" in out1

    c2 = run_agent_cli(Namespace(agent_cmd="policy", policy_cmd="verify"))
    assert c2 == 0
    out2 = capsys.readouterr().out
    assert "MS8_AGENT_POLICY_VERIFY" in out2
    assert "status=PASS" in out2


def test_policy_verify_fail_when_profile_invalid(tmp_path: Path, monkeypatch) -> None:
    home_dir = tmp_path / "home"
    home_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("MS8_HOME", str(tmp_path / "runtime"))
    monkeypatch.chdir(tmp_path)
    init_agent_native("DEFAULT_SAFE", cwd=tmp_path, force=True, dry_run=False, confirm=False)

    policy = (tmp_path / "runtime") / "agent_native" / "agent_policy.json"
    payload = json.loads(policy.read_text(encoding="utf-8"))
    payload["permission_profile"] = "BROKEN"
    policy.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    code = run_agent_cli(Namespace(agent_cmd="policy", policy_cmd="verify"))
    assert code == 1
