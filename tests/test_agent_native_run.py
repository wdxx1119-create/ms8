from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from ms8.agent_native.agent_cli import run_agent_cli
from ms8.agent_native.onboarding import init_agent_native


def test_agent_run_check_and_report(tmp_path: Path, monkeypatch, capsys) -> None:
    home_dir = tmp_path / "home"
    home_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("MS8_HOME", str(tmp_path / "runtime"))
    monkeypatch.setenv("MS8_BOOTSTRAP_ON_FIRST_RUN", "0")
    monkeypatch.chdir(tmp_path)
    init_agent_native("DEFAULT_SAFE", cwd=tmp_path, force=True, dry_run=False, confirm=False)

    c1 = run_agent_cli(Namespace(agent_cmd="run", run_cmd="check", no_repair_preview=True))
    assert c1 in {0, 1}
    out1 = capsys.readouterr().out
    assert "MS8_AGENT_RUN_CHECK" in out1
    assert "doctor_exit_code=" in out1
    assert "issue_found=" in out1

    c2 = run_agent_cli(Namespace(agent_cmd="run", run_cmd="report", no_redact=False))
    assert c2 == 0
    out2 = capsys.readouterr().out
    assert "MS8_AGENT_RUN_REPORT" in out2
    assert "critical_issue=" in out2

    c3 = run_agent_cli(Namespace(agent_cmd="run", run_cmd="daily", no_repair_preview=True, no_redact=False))
    assert c3 in {0, 1}
    out3 = capsys.readouterr().out
    assert "MS8_AGENT_RUN_DAILY" in out3
    assert "check=" in out3
    assert "report=" in out3
    assert "verbose_output=False" in out3
    assert "summary_8=[" in out3
    assert "  1.status=" in out3
    assert "  8.next_action=" in out3
    assert "doctor_overall" in out3
    assert "critical_issue" in out3

    c4 = run_agent_cli(
        Namespace(agent_cmd="run", run_cmd="daily", no_repair_preview=True, no_redact=False, verbose_output=True)
    )
    assert c4 in {0, 1}
    out4 = capsys.readouterr().out
    assert "verbose_output=True" in out4
    assert "doctor_exit_code" in out4


def test_agent_run_install_report(tmp_path: Path, monkeypatch, capsys) -> None:
    home_dir = tmp_path / "home"
    home_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("MS8_HOME", str(tmp_path / "runtime"))
    monkeypatch.setenv("MS8_BOOTSTRAP_ON_FIRST_RUN", "0")
    monkeypatch.chdir(tmp_path)

    c1 = run_agent_cli(Namespace(agent_cmd="run", run_cmd="install", profile="DEFAULT_SAFE", confirm=False))
    assert c1 == 0
    out1 = capsys.readouterr().out
    assert "MS8_FIRST_INSTALL_REPORT" in out1
    assert "permission_profile=DEFAULT_SAFE" in out1
    assert "learned_tasks=usage,ops,check,report" in out1

    c2 = run_agent_cli(Namespace(agent_cmd="run", run_cmd="install", profile="TRUSTED_AGENT", confirm=False))
    assert c2 == 1
    out2 = capsys.readouterr().out
    assert "status=NEEDS_CONFIRM" in out2
