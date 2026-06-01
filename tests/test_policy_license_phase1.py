from __future__ import annotations

import base64
import json

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ms8.engine_core import policy_engine_loader as loader


def _write_signed_license(path, private_key: Ed25519PrivateKey, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    sig = private_key.sign(body)
    payload_with_sig = dict(payload)
    payload_with_sig["sig"] = base64.b64encode(sig).decode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload_with_sig, ensure_ascii=False, indent=2), encoding="utf-8")


def test_policy_license_valid_allows_closed(monkeypatch, tmp_path) -> None:
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    monkeypatch.setenv("MS8_POLICY_LICENSE_ENABLED", "1")
    monkeypatch.setenv("MS8_POLICY_LICENSE_PUBKEY_PEM", pub)
    monkeypatch.setenv("MS8_POLICY_BACKEND", "closed")
    monkeypatch.setenv("MS8_POLICY_MODULE", "module_that_does_not_exist_for_test")
    _write_signed_license(
        tmp_path / ".ms8" / "config" / "policy_license",
        priv,
        {"sub": "acme", "iat": 100, "exp": 0, "devices": [], "kid": "k1"},
    )
    loader.reset_policy_engine_for_tests()
    _ = loader.get_policy_engine()
    st = loader.get_policy_backend_status()
    # closed module is missing, so fallback reason should still be closed-load-related
    assert "closed_load_failed" in str(st.get("policy_fallback_reason", ""))
    lic = st.get("policy_license", {})
    assert isinstance(lic, dict)
    assert lic.get("status") == "valid"


def test_policy_license_invalid_signature_denies_closed(monkeypatch, tmp_path) -> None:
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    monkeypatch.setenv("MS8_POLICY_LICENSE_ENABLED", "1")
    monkeypatch.setenv("MS8_POLICY_LICENSE_PUBKEY_PEM", pub)
    monkeypatch.setenv("MS8_POLICY_BACKEND", "closed")
    # write tampered signature
    p = tmp_path / ".ms8" / "config" / "policy_license"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(
            {"sub": "acme", "iat": 100, "exp": 0, "devices": [], "kid": "k1", "sig": "AAAA"},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    loader.reset_policy_engine_for_tests()
    engine = loader.get_policy_engine()
    assert engine.backend_name == "open"
    st = loader.get_policy_backend_status()
    assert str(st.get("policy_fallback_reason", "")).startswith("license_denied:")
    lic = st.get("policy_license", {})
    assert isinstance(lic, dict)
    assert lic.get("status") == "invalid"


def test_policy_license_expired_without_grace_denies_closed(monkeypatch, tmp_path) -> None:
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    monkeypatch.setenv("MS8_POLICY_LICENSE_ENABLED", "1")
    monkeypatch.setenv("MS8_POLICY_LICENSE_PUBKEY_PEM", pub)
    monkeypatch.setenv("MS8_POLICY_BACKEND", "closed")
    monkeypatch.setenv("MS8_POLICY_LICENSE_GRACE_DAYS", "0")
    monkeypatch.setenv("MS8_POLICY_LICENSE_NOW_TS", "200")
    _write_signed_license(
        tmp_path / ".ms8" / "config" / "policy_license",
        priv,
        {"sub": "acme", "iat": 100, "exp": 150, "devices": [], "kid": "k1"},
    )
    loader.reset_policy_engine_for_tests()
    engine = loader.get_policy_engine()
    assert engine.backend_name == "open"
    st = loader.get_policy_backend_status()
    assert str(st.get("policy_fallback_reason", "")).startswith("license_denied:license_expired")

