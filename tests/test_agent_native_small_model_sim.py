from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from ms8.agent_native.agent_cli import run_agent_cli
from ms8.agent_native.onboarding import init_agent_native, show_task
from ms8.agent_native.report import block


def test_templates_are_3b_friendly(tmp_path: Path, monkeypatch) -> None:
    home_dir = tmp_path / "home"
    home_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("MS8_HOME", str(tmp_path / "runtime"))
    monkeypatch.chdir(tmp_path)
    init_agent_native("DEFAULT_SAFE", cwd=tmp_path, force=True, dry_run=False, confirm=False)

    install = show_task(tmp_path, "install")["content"]
    assert "DECISION:" in install
    assert "[RUN_RESULT]ms8_version_check" in install
    assert "[VAR]ms8_already_installed" in install
    assert "RUN python -m ms8 version -> ms8_version_check" in install
    assert "Do not fail the whole task only because ms8_version_check failed." in install
    assert "ASK_USER:" in install
    assert "ALLOWED_COMMANDS:" in install
    assert "python -m ms8 version" in install
    assert "python -m ms8 doctor" in install
    assert "python -m ms8 engine status --format text" in install
    assert "setuptools.build_meta unavailable" in install
    assert "VALID_CHOICES: [DEFAULT_SAFE, TRUSTED_AGENT]" in install
    assert "If user did not answer and interactive input is unavailable -> STOP NEEDS_CONFIRM" in install
    assert "If user input is invalid -> STOP NEEDS_CONFIRM" in install
    assert "status=PASS|FAIL|NEEDS_CONFIRM|ALREADY_INSTALLED" in install
    assert "permission_profile=" in install

    ops = show_task(tmp_path, "ops")["content"]
    assert "IMPORTANT:" in ops
    assert "Must execute STEP 1 -> STEP 2 -> STEP 3 in order." in ops
    assert "issue_found=true if doctor output contains" in ops
    assert "ops self-repair-run --mode dry-run" in ops
    assert 'critical_issue=true if doctor output contains "Overall: degraded" or "Overall: FAIL".' in ops
    assert "Read permission_profile from MS8_HOME/agent_native/agent_policy.json." in ops

    usage = show_task(tmp_path, "usage")["content"]
    assert 'EXAMPLE: python -m ms8 ask "release process"' in usage
    assert 'EXAMPLE: python -m ms8 ask "记住: 用户偏好中文日志输出"' in usage

    readme = (tmp_path / ".ms8" / "agent_native" / "README_AGENT.md").read_text(encoding="utf-8")
    assert "## 权限模式" in readme
    assert "## 快速开始" in readme
    assert "所有项目共享" in readme
    assert "MS8_HOME/agent_native/agent_policy.json" in readme
    assert "切换权限模式会影响所有项目" in readme
    assert "agent remove` 默认不会删除全局权限策略" in readme
    assert "python -m ms8 agent task show check" in readme
    assert "python -m ms8 agent task show report" in readme
    assert "## 安装环境前置" in readme
    assert "setuptools.build_meta" in readme


def test_block_formats_lists_multiline() -> None:
    text = block("X", {"tasks": ["install", "ops", "usage"]})
    assert "tasks=[" in text
    assert "  install" in text
    assert "  ops" in text
    assert "  usage" in text


def test_task_show_no_duplicate_content(tmp_path: Path, monkeypatch, capsys) -> None:
    home_dir = tmp_path / "home"
    home_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("MS8_HOME", str(tmp_path / "runtime"))
    monkeypatch.chdir(tmp_path)
    init_agent_native("DEFAULT_SAFE", cwd=tmp_path, force=True, dry_run=False, confirm=False)

    code = run_agent_cli(Namespace(agent_cmd="task", task_cmd="show", name="usage"))
    assert code == 0
    out = capsys.readouterr().out
    assert "MS8_AGENT_TASK_SHOW" in out
    assert out.count("TASK use_ms8_memory") == 1
