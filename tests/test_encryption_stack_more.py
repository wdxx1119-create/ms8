from __future__ import annotations

import json
from pathlib import Path

import pytest

from ms8.engine_core.security.encryption import recovery
from ms8.engine_core.security.encryption.crypto_manager import CryptoError, CryptoManager
from ms8.engine_core.security.encryption.file_crypto import FileCryptoError, decrypt_bytes, encrypt_bytes, is_encrypted_blob
from ms8.engine_core.security.encryption.key_manager import KeyManager, KeyManagerError


def _cfg(base: Path, *, enabled: bool = False, targets: list[str] | None = None) -> dict:
    return {
        "workspace_dir": str(base),
        "memory_dir": str(base / "memory"),
        "settings": {
            "memory": {
                "security": {
                    "enabled": enabled,
                    "encrypted_targets": targets or [],
                    "use_keychain": False,
                }
            }
        },
    }


def test_file_crypto_roundtrip_and_invalid_payload() -> None:
    dek = b"\x01" * 32
    raw = b"hello-encryption"
    blob = encrypt_bytes(raw, file_type="text", dek=dek, kdf="pbkdf2_hmac_sha256")
    assert is_encrypted_blob(blob) is True
    assert decrypt_bytes(blob, dek) == raw
    with pytest.raises(FileCryptoError):
        decrypt_bytes(b"not-encrypted", dek)
    with pytest.raises(Exception):
        decrypt_bytes(blob[:-3], dek)


def test_key_manager_wrap_unwrap_validate_and_file_material(tmp_path: Path) -> None:
    km = KeyManager(tmp_path / "km.json", tmp_path / "recovery.json", use_keychain=False)
    with pytest.raises(KeyManagerError):
        km.create_master_secret("short")
    meta = km.create_master_secret("very-strong-password")
    dek = km.generate_data_key()
    wrapped = km.wrap_data_key(dek, "very-strong-password", meta)
    assert km.unwrap_data_key(
        {"kdf": wrapped.kdf, "wrap_nonce": wrapped.wrap_nonce, "wrapped_dek": wrapped.wrapped_dek},
        "very-strong-password",
    ) == dek
    assert km.validate_master_password(
        {"kdf": wrapped.kdf, "wrap_nonce": wrapped.wrap_nonce, "wrapped_dek": wrapped.wrapped_dek},
        "bad-password",
    ) is False
    km.save_material(wrapped, enabled=True)
    loaded = km.load_material()
    assert loaded.get("wrapped_dek")
    assert loaded.get("kdf")


def test_key_manager_recovery_material_roundtrip(tmp_path: Path) -> None:
    km = KeyManager(tmp_path / "km.json", tmp_path / "recovery.json", use_keychain=False)
    dek = km.generate_data_key()
    rk = km.generate_recovery_key()
    material = km.create_recovery_material(dek, rk)
    assert km.recover_data_key(material, rk) == dek
    with pytest.raises(Exception):
        km.recover_data_key(material, "wrong-recovery-key")


def test_crypto_manager_encrypt_decrypt_and_locking(tmp_path: Path) -> None:
    base = tmp_path
    (base / "memory").mkdir(parents=True, exist_ok=True)
    (base / "MEMORY.md").write_text("hello", encoding="utf-8")
    cm = CryptoManager(_cfg(base, enabled=False, targets=["MEMORY.md"]))
    assert cm.is_enabled() is False
    plain = b"abc"
    assert cm.encrypt_before_write(plain, "text", base / "MEMORY.md") == plain
    assert cm.decrypt_after_read(plain, base / "MEMORY.md") == plain

    enabled_cfg = _cfg(base, enabled=False, targets=["MEMORY.md"])
    cm2 = CryptoManager(enabled_cfg)
    assert cm2.enable_encryption("very-strong-password")["status"] == "success"
    assert cm2.is_enabled() is True
    assert cm2.is_unlocked() is True

    enc = cm2.encrypt_before_write(b"secret", "text", base / "MEMORY.md")
    assert is_encrypted_blob(enc) is True
    assert cm2.decrypt_after_read(enc, base / "MEMORY.md") == b"secret"
    cm2.lock()
    with pytest.raises(CryptoError):
        cm2.encrypt_before_write(b"x", "text", base / "MEMORY.md")


def test_crypto_manager_migration_and_disable_flow(tmp_path: Path) -> None:
    base = tmp_path
    memory = base / "memory"
    memory.mkdir(parents=True, exist_ok=True)
    p1 = base / "MEMORY.md"
    p2 = memory / "auto_memory_records.jsonl"
    p1.write_text("m1", encoding="utf-8")
    p2.write_text("m2", encoding="utf-8")
    targets = ["MEMORY.md", "memory/auto_memory_records.jsonl"]
    cm = CryptoManager(_cfg(base, enabled=False, targets=targets))
    out = cm.enable_encryption("very-strong-password")
    assert out["status"] == "success"
    assert is_encrypted_blob(p1.read_bytes()) is True
    assert is_encrypted_blob(p2.read_bytes()) is True
    disabled = cm.disable_encryption("very-strong-password")
    assert disabled["status"] == "success"
    assert p1.read_text(encoding="utf-8") == "m1"
    assert p2.read_text(encoding="utf-8") == "m2"


def test_recovery_helper_success_and_error_paths(tmp_path: Path) -> None:
    base = tmp_path
    (base / "memory").mkdir(parents=True, exist_ok=True)
    cm = CryptoManager(_cfg(base, enabled=False, targets=["MEMORY.md"]))
    enabled = cm.enable_encryption("very-strong-password")
    rk = enabled["recovery_key"]
    assert recovery.recover_with_recovery_key(cm, rk, "another-strong-pass")["status"] == "success"
    with pytest.raises(Exception):
        recovery.recover_with_recovery_key(cm, "wrong", "x" * 12)
    # clear material to hit missing branch
    cm.recovery_path.write_text("{}", encoding="utf-8")
    miss = recovery.recover_with_recovery_key(cm, rk, "x" * 12)
    assert miss["status"] == "error"
    assert miss["reason"] == "recovery_material_missing"


def test_crypto_manager_load_state_invalid_json_and_plaintext_reject(tmp_path: Path) -> None:
    base = tmp_path
    (base / "memory").mkdir(parents=True, exist_ok=True)
    cm = CryptoManager(_cfg(base, enabled=True, targets=["MEMORY.md"]))
    cm.state_path.write_text("{bad json", encoding="utf-8")
    cm_reload = CryptoManager(_cfg(base, enabled=True, targets=["MEMORY.md"]))
    assert isinstance(cm_reload.status(), dict)
    cm_reload.enable_encryption("very-strong-password")
    with pytest.raises(CryptoError):
        cm_reload.decrypt_after_read(b"plaintext", base / "MEMORY.md", allow_plaintext=False)


def test_key_manager_keychain_set_get_error_paths(monkeypatch, tmp_path: Path) -> None:
    km = KeyManager(tmp_path / "km.json", tmp_path / "recovery.json", use_keychain=True)
    km._security_bin = "/usr/bin/security"

    class _P:
        def __init__(self, code: int, out: str = "", err: str = "") -> None:
            self.returncode = code
            self.stdout = out
            self.stderr = err

    monkeypatch.setattr("subprocess.run", lambda *a, **k: _P(1, "", "boom"))
    with pytest.raises(KeyManagerError):
        km._keychain_set_material({"a": 1})  # noqa: SLF001
    with pytest.raises(KeyManagerError):
        km._keychain_get_material()  # noqa: SLF001

    monkeypatch.setattr("subprocess.run", lambda *a, **k: _P(0, "", ""))
    with pytest.raises(KeyManagerError):
        km._keychain_get_material()  # noqa: SLF001

    monkeypatch.setattr("subprocess.run", lambda *a, **k: _P(0, "{bad}", ""))
    with pytest.raises(KeyManagerError):
        km._keychain_get_material()  # noqa: SLF001

    monkeypatch.setattr("subprocess.run", lambda *a, **k: _P(0, json.dumps({"wrapped_dek": "x", "kdf": {}}), ""))
    material = km._keychain_get_material()  # noqa: SLF001
    assert material["wrapped_dek"] == "x"


def test_crypto_manager_should_protect_patterns(tmp_path: Path) -> None:
    base = tmp_path
    (base / "memory").mkdir(parents=True, exist_ok=True)
    abs_target = base / "abs.txt"
    abs_target.write_text("x", encoding="utf-8")
    cfg = _cfg(base, enabled=False, targets=[str(abs_target), "memory/", "MEMORY.md"])
    cm = CryptoManager(cfg)
    assert cm.should_protect_path(abs_target) is True
    assert cm.should_protect_path(base / "memory" / "x.json") is True
    assert cm.should_protect_path(base / "MEMORY.md") is True
    assert cm.should_protect_path(base / "other.txt") is False
