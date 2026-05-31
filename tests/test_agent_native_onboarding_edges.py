from __future__ import annotations

import json
from pathlib import Path

from ms8.agent_native import onboarding


def test_migrate_policy_canonical_exists_without_force(tmp_path: Path, monkeypatch) -> None:
    runtime_home = tmp_path / "runtime"
    home_dir = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("MS8_HOME", str(runtime_home))
    (runtime_home / "agent_native").mkdir(parents=True, exist_ok=True)
    (home_dir / ".ms8_runtime" / "agent_native").mkdir(parents=True, exist_ok=True)

    canonical = runtime_home / "agent_native" / "agent_policy.json"
    legacy = home_dir / ".ms8_runtime" / "agent_native" / "agent_policy.json"
    canonical.write_text('{"permission_profile":"DEFAULT_SAFE"}', encoding="utf-8")
    legacy.write_text('{"permission_profile":"TRUSTED_AGENT"}', encoding="utf-8")

    out = onboarding.migrate_policy_path(dry_run=False, force=False, cleanup_legacy=False)
    assert out["status"] == "SKIPPED"
    assert out["reason"] == "canonical_exists_use_force"


def test_init_profile_validation_and_trusted_confirm(tmp_path: Path, monkeypatch) -> None:
    runtime_home = tmp_path / "runtime"
    home_dir = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("MS8_HOME", str(runtime_home))
    monkeypatch.chdir(tmp_path)

    bad = onboarding.init_agent_native("bad-profile", cwd=tmp_path)
    assert bad["status"] == "FAIL"

    trusted_without_confirm = onboarding.init_agent_native("TRUSTED_AGENT", cwd=tmp_path, confirm=False)
    assert trusted_without_confirm["status"] == "NEEDS_CONFIRM"


def test_read_permission_invalid_json_and_verify_schema_not_object(tmp_path: Path, monkeypatch) -> None:
    runtime_home = tmp_path / "runtime"
    home_dir = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("MS8_HOME", str(runtime_home))
    policy_dir = runtime_home / "agent_native"
    policy_dir.mkdir(parents=True, exist_ok=True)
    policy_path = policy_dir / "agent_policy.json"

    policy_path.write_text("{bad-json", encoding="utf-8")
    out = onboarding.read_permission()
    assert out["status"] == "FAIL"

    policy_path.write_text("[]", encoding="utf-8")
    out2 = onboarding.verify_permission_schema()
    assert out2["status"] == "FAIL"
    assert out2["error_code"] == "E_POLICY_NOT_OBJECT"


def test_verify_permission_schema_missing_fields(tmp_path: Path, monkeypatch) -> None:
    runtime_home = tmp_path / "runtime"
    home_dir = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("MS8_HOME", str(runtime_home))
    policy_dir = runtime_home / "agent_native"
    policy_dir.mkdir(parents=True, exist_ok=True)
    policy_path = policy_dir / "agent_policy.json"
    policy_path.write_text(json.dumps({"permission_profile": "DEFAULT_SAFE"}), encoding="utf-8")

    out = onboarding.verify_permission_schema()
    assert out["status"] == "FAIL"
    assert "policy_version" in out["missing"]


def test_remove_agent_native_missing_dir_is_pass(tmp_path: Path) -> None:
    out = onboarding.remove_agent_native(tmp_path)
    assert out["status"] == "PASS"
    assert out["removed_or_archived"] == ""

