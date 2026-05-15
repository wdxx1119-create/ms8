from __future__ import annotations

from pathlib import Path

from ms8.agent_native.onboarding import init_agent_native, list_tasks, show_task


def test_agent_task_list_and_show(tmp_path: Path, monkeypatch) -> None:
    home_dir = tmp_path / "home"
    home_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("MS8_HOME", str(tmp_path / "runtime"))
    monkeypatch.chdir(tmp_path)
    init_agent_native("DEFAULT_SAFE", cwd=tmp_path, force=True, dry_run=False, confirm=False)

    listed = list_tasks(tmp_path)
    assert listed["status"] == "PASS"
    assert set(listed["tasks"]) == {"install", "ops", "check", "report", "usage"}

    shown = show_task(tmp_path, "usage")
    assert shown["status"] == "PASS"
    assert "TASK use_ms8_memory" in shown["content"]

    shown_check = show_task(tmp_path, "check")
    assert shown_check["status"] == "PASS"
    assert "TASK ms8_daily_check" in shown_check["content"]

    shown_report = show_task(tmp_path, "report")
    assert shown_report["status"] == "PASS"
    assert "TASK ms8_daily_report" in shown_report["content"]
