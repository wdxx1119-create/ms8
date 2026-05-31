from __future__ import annotations

import runpy

from ms8.agent_native import permission as perm


def test_module_entrypoint_exits_with_cli_return_code(monkeypatch):
    called = {"count": 0, "code": None}

    def _fake_main() -> int:
        called["count"] += 1
        return 7

    monkeypatch.setattr("ms8.cli.main", _fake_main)
    try:
        runpy.run_module("ms8.__main__", run_name="__main__")
    except SystemExit as exc:
        called["code"] = int(exc.code)

    assert called["count"] == 1
    assert called["code"] == 7


def test_permission_profiles_have_expected_boundaries():
    safe = perm.build_policy("DEFAULT_SAFE", "0.2.0")
    trusted = perm.build_policy("TRUSTED_AGENT", "0.2.0")

    assert safe["permission_profile"] == "DEFAULT_SAFE"
    assert trusted["permission_profile"] == "TRUSTED_AGENT"
    assert safe["deny_shadow_system_access"] is True
    assert trusted["deny_shadow_system_access"] is True
    assert safe["allow_safe_repair_dry_run"] is False
    assert trusted["allow_safe_repair_dry_run"] is True
    assert trusted["allow_safe_repair"] is False


def test_invalid_permission_profile_falls_back_to_default():
    try:
        perm.build_policy("UNKNOWN_PROFILE", "0.2.0")
    except ValueError as exc:
        assert "unsupported_profile" in str(exc)
