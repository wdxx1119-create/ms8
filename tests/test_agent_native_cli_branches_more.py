from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from ms8.agent_native.agent_cli import run_agent_cli


def test_agent_cli_unknown_top_command_returns_2(capsys) -> None:
    code = run_agent_cli(Namespace(agent_cmd="unknown"))
    out = capsys.readouterr().out
    assert code == 2
    assert "ms8 agent: choose" in out


def test_agent_cli_policy_without_subcommand_returns_2(capsys) -> None:
    code = run_agent_cli(Namespace(agent_cmd="policy", policy_cmd=""))
    out = capsys.readouterr().out
    assert code == 2
    assert "ms8 agent policy: choose verify" in out


def test_agent_cli_run_without_subcommand_returns_2(capsys) -> None:
    code = run_agent_cli(Namespace(agent_cmd="run", run_cmd=""))
    out = capsys.readouterr().out
    assert code == 2
    assert "ms8 agent run: choose install|check|report|daily|absorb" in out


def test_agent_cli_task_without_subcommand_returns_2(capsys) -> None:
    code = run_agent_cli(Namespace(agent_cmd="task", task_cmd=""))
    out = capsys.readouterr().out
    assert code == 2
    assert "ms8 agent task: choose list|verify|show" in out


def test_agent_cli_task_show_missing_returns_1(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    code = run_agent_cli(Namespace(agent_cmd="task", task_cmd="show", name="usage"))
    assert code == 1


def test_agent_cli_permission_upgrade_needs_confirm(tmp_path: Path, monkeypatch, capsys) -> None:
    home_dir = tmp_path / "home"
    home_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("MS8_HOME", str(tmp_path / "runtime"))
    monkeypatch.chdir(tmp_path)
    # no confirm should require confirmation
    code = run_agent_cli(
        Namespace(agent_cmd="permission", permission_cmd="upgrade", to="TRUSTED_AGENT", confirm=False, dry_run=False)
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "NEEDS_CONFIRM" in out
