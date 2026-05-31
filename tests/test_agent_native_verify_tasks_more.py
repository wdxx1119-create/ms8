from __future__ import annotations

from pathlib import Path

from ms8.agent_native import onboarding
from ms8.agent_native.onboarding import init_agent_native
from ms8.agent_native.task_spec import TASK_FILE_MAP, TASK_VERSION


def test_verify_tasks_missing_directory(tmp_path: Path) -> None:
    out = onboarding.verify_tasks(tmp_path)
    assert out["ok"] is False
    assert out["status"] == "MISSING"
    assert out["error_code"] == "E_TASK_DIR_MISSING"


def test_verify_tasks_missing_and_version_mismatch(monkeypatch, tmp_path: Path) -> None:
    runtime_home = tmp_path / "runtime"
    monkeypatch.setattr(onboarding, "get_ms8_home", lambda: runtime_home)

    init_agent_native("DEFAULT_SAFE", cwd=tmp_path, force=True, dry_run=False, confirm=False)
    project_dir = tmp_path / ".ms8" / "agent_native"

    usage = project_dir / TASK_FILE_MAP["usage"]
    usage.unlink()
    install = project_dir / TASK_FILE_MAP["install"]
    install.write_text("TASK_VERSION: 999\nBROKEN", encoding="utf-8")

    out = onboarding.verify_tasks(tmp_path)
    assert out["ok"] is False
    assert out["status"] == "FAIL"
    assert out["error_code"] == "E_TASK_VERIFY_FAILED"
    details = {row["task"]: row for row in out["details"]}
    assert details["usage"]["error_code"] == "E_TASK_FILE_MISSING"
    assert details["install"]["error_code"] == "E_TASK_VERSION_MISMATCH"
    assert details["install"]["expected"] == f"TASK_VERSION: {TASK_VERSION}"

