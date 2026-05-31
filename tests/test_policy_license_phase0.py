from __future__ import annotations

from ms8.engine_core import policy_engine_loader as loader


def test_policy_license_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("MS8_POLICY_LICENSE_ENABLED", raising=False)
    monkeypatch.setenv("MS8_POLICY_BACKEND", "open")
    loader.reset_policy_engine_for_tests()
    _ = loader.get_policy_engine()
    status = loader.get_policy_backend_status()
    lic = status.get("policy_license", {})
    assert isinstance(lic, dict)
    assert lic.get("status") == "disabled"
    assert lic.get("enabled") is False


def test_policy_license_missing_is_non_blocking(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    monkeypatch.setenv("MS8_POLICY_LICENSE_ENABLED", "1")
    monkeypatch.setenv("MS8_POLICY_BACKEND", "open")
    loader.reset_policy_engine_for_tests()
    engine = loader.get_policy_engine()
    assert engine.backend_name == "open"
    status = loader.get_policy_backend_status()
    lic = status.get("policy_license", {})
    assert lic.get("status") == "missing"
    assert lic.get("reason_code") == "license_file_missing"

