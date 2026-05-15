from __future__ import annotations

import json
from pathlib import Path

from ms8.agent_native.onboarding import init_agent_native, read_permission


def test_agent_init_default_safe(tmp_path: Path, monkeypatch) -> None:
    runtime_home = tmp_path / "runtime"
    home_dir = tmp_path / "home"
    home_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("MS8_HOME", str(runtime_home))
    monkeypatch.chdir(tmp_path)

    out = init_agent_native("DEFAULT_SAFE", cwd=tmp_path, dry_run=False, force=True, confirm=False)
    assert out["status"] == "PASS"

    project_dir = tmp_path / ".ms8" / "agent_native"
    assert (project_dir / "install.task").exists()
    assert (project_dir / "ops.task").exists()
    assert (project_dir / "check.task").exists()
    assert (project_dir / "report.task").exists()
    assert (project_dir / "usage.task").exists()
    assert (project_dir / "README_AGENT.md").exists()

    perm = read_permission()
    assert perm["status"] == "PASS"
    policy = perm["policy"]
    assert policy["permission_profile"] == "DEFAULT_SAFE"
    assert policy["deny_shadow_system_access"] is True
    assert "ms8_version" in policy
    assert "agent_id" in policy
    assert "created_at" in policy

    policy_path = Path(perm["policy_path"])
    payload = json.loads(policy_path.read_text(encoding="utf-8"))
    assert payload["permission_profile"] == "DEFAULT_SAFE"
