from __future__ import annotations

import json
from pathlib import Path

from ms8.agent_native.onboarding import migrate_policy_path


def test_migrate_policy_path_from_legacy(tmp_path: Path, monkeypatch) -> None:
    home_dir = tmp_path / "home"
    runtime_home = tmp_path / "runtime"
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("MS8_HOME", str(runtime_home))
    home_dir.mkdir(parents=True, exist_ok=True)
    (home_dir / ".ms8_runtime" / "agent_native").mkdir(parents=True, exist_ok=True)

    legacy = home_dir / ".ms8_runtime" / "agent_native" / "agent_policy.json"
    legacy.write_text(json.dumps({"permission_profile": "DEFAULT_SAFE"}), encoding="utf-8")

    out = migrate_policy_path(dry_run=False, force=False)
    assert out["status"] == "PASS"
    canonical = runtime_home / "agent_native" / "agent_policy.json"
    assert canonical.exists()
    payload = json.loads(canonical.read_text(encoding="utf-8"))
    assert payload["permission_profile"] == "DEFAULT_SAFE"


def test_migrate_policy_path_cleanup_legacy(tmp_path: Path, monkeypatch) -> None:
    home_dir = tmp_path / "home"
    runtime_home = tmp_path / "runtime"
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("MS8_HOME", str(runtime_home))
    home_dir.mkdir(parents=True, exist_ok=True)
    (home_dir / ".ms8_runtime" / "agent_native").mkdir(parents=True, exist_ok=True)

    legacy = home_dir / ".ms8_runtime" / "agent_native" / "agent_policy.json"
    legacy.write_text(json.dumps({"permission_profile": "DEFAULT_SAFE"}), encoding="utf-8")

    out = migrate_policy_path(dry_run=False, force=False, cleanup_legacy=True)
    assert out["status"] == "PASS"
    assert out["legacy_removed"] is True
    assert not legacy.exists()
