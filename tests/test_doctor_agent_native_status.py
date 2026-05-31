from __future__ import annotations

import json
from pathlib import Path

from ms8 import doctor


def test_agent_native_status_prefers_canonical_policy(monkeypatch, tmp_path: Path) -> None:
    runtime_home = tmp_path / "runtime"
    home_dir = tmp_path / "home"
    monkeypatch.setattr(doctor, "get_ms8_home", lambda: runtime_home)
    monkeypatch.setattr(doctor.Path, "home", staticmethod(lambda: home_dir))
    monkeypatch.chdir(tmp_path)

    (runtime_home / "agent_native").mkdir(parents=True, exist_ok=True)
    (home_dir / ".ms8_runtime" / "agent_native").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".ms8" / "agent_native").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".ms8" / "agent_native" / "install.task").write_text("x", encoding="utf-8")
    (tmp_path / ".ms8" / "agent_native" / "ops.task").write_text("x", encoding="utf-8")
    (tmp_path / ".ms8" / "agent_native" / "usage.task").write_text("x", encoding="utf-8")

    (home_dir / ".ms8_runtime" / "agent_native" / "agent_policy.json").write_text(
        json.dumps({"permission_profile": "TRUSTED_AGENT"}),
        encoding="utf-8",
    )
    (runtime_home / "agent_native" / "agent_policy.json").write_text(
        json.dumps({"permission_profile": "DEFAULT_SAFE"}),
        encoding="utf-8",
    )

    out = doctor._agent_native_status()
    assert out["policy"] == "PRESENT"
    assert out["permission_profile"] == "DEFAULT_SAFE"
    assert out["agent_native_status"] == "OK"


def test_agent_native_status_missing_tasks_and_bad_policy_json(monkeypatch, tmp_path: Path) -> None:
    runtime_home = tmp_path / "runtime"
    home_dir = tmp_path / "home"
    monkeypatch.setattr(doctor, "get_ms8_home", lambda: runtime_home)
    monkeypatch.setattr(doctor.Path, "home", staticmethod(lambda: home_dir))
    monkeypatch.chdir(tmp_path)

    (runtime_home / "agent_native").mkdir(parents=True, exist_ok=True)
    (runtime_home / "agent_native" / "agent_policy.json").write_text("{bad json", encoding="utf-8")
    (tmp_path / ".ms8" / "agent_native").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".ms8" / "agent_native" / "install.task").write_text("x", encoding="utf-8")

    out = doctor._agent_native_status()
    assert out["permission_profile"] == "N/A"
    assert "install=P" in out["task_files"]
    assert "ops=M" in out["task_files"]
    assert out["agent_native_status"] == "NEEDS_INIT"

