from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

try:
    from argon2.low_level import Type as Argon2Type
    from argon2.low_level import hash_secret_raw

    _HAS_ARGON2 = True
except ImportError:
    _HAS_ARGON2 = False


@dataclass
class WrappedKeyBundle:
    wrapped_dek: str
    wrap_nonce: str
    kdf: dict[str, Any]


class KeyManagerError(Exception):
    pass


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.b64decode(value.encode("ascii"))


class KeyManager:
    """Manage DEK wrapping/unwrapping and recovery material."""

    def __init__(
        self,
        key_material_path: Path,
        recovery_path: Path,
        *,
        use_keychain: bool = False,
        keychain_service: str = "ms8-memory",
        keychain_account: str = "master-key",
    ) -> None:
        self.key_material_path = key_material_path
        self.recovery_path = recovery_path
        self.key_material_path.parent.mkdir(parents=True, exist_ok=True)
        self.recovery_path.parent.mkdir(parents=True, exist_ok=True)
        self.use_keychain = bool(use_keychain)
        self.keychain_service = str(keychain_service or "ms8-memory")
        self.keychain_account = str(keychain_account or "master-key")
        self._security_bin = shutil.which("security")

    def _keychain_available(self) -> bool:
        return bool(self.use_keychain and self._security_bin)

    def _keychain_set_material(self, payload: dict[str, Any]) -> None:
        if not self._keychain_available():
            raise KeyManagerError("keychain_unavailable")
        secret = json.dumps(payload, ensure_ascii=False)
        cmd = [
            self._security_bin or "security",
            "add-generic-password",
            "-a",
            self.keychain_account,
            "-s",
            self.keychain_service,
            "-w",
            secret,
            "-U",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise KeyManagerError(proc.stderr.strip() or "keychain_set_failed")

    def _keychain_get_material(self) -> dict[str, Any]:
        if not self._keychain_available():
            raise KeyManagerError("keychain_unavailable")
        cmd = [
            self._security_bin or "security",
            "find-generic-password",
            "-a",
            self.keychain_account,
            "-s",
            self.keychain_service,
            "-w",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise KeyManagerError(proc.stderr.strip() or "keychain_get_failed")
        raw = (proc.stdout or "").strip()
        if not raw:
            raise KeyManagerError("keychain_material_empty")
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                return obj
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise KeyManagerError("keychain_material_invalid") from exc
        raise KeyManagerError("keychain_material_invalid")

    def create_master_secret(self, master_password: str) -> dict[str, Any]:
        if not master_password or len(master_password) < 8:
            raise KeyManagerError("master_password_too_short")
        salt = os.urandom(16)
        if _HAS_ARGON2:
            return {
                "kdf": "argon2id",
                "salt": _b64(salt),
                "time_cost": 2,
                "memory_cost": 65536,
                "parallelism": 2,
                "length": 32,
            }
        return {
            "kdf": "pbkdf2_hmac_sha256",
            "salt": _b64(salt),
            "iterations": 200_000,
            "length": 32,
        }

    def _derive_master_key(self, password: str, kdf_meta: dict[str, Any]) -> bytes:
        salt = _unb64(str(kdf_meta["salt"]))
        mode = str(kdf_meta.get("kdf", "pbkdf2_hmac_sha256"))
        length = int(kdf_meta.get("length", 32))
        if mode == "argon2id" and _HAS_ARGON2:
            return hash_secret_raw(
                secret=password.encode("utf-8"),
                salt=salt,
                time_cost=int(kdf_meta.get("time_cost", 2)),
                memory_cost=int(kdf_meta.get("memory_cost", 65536)),
                parallelism=int(kdf_meta.get("parallelism", 2)),
                hash_len=length,
                type=Argon2Type.ID,
            )
        return hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            int(kdf_meta.get("iterations", 200_000)),
            dklen=length,
        )

    def generate_data_key(self) -> bytes:
        return os.urandom(32)

    def wrap_data_key(self, dek: bytes, master_password: str, kdf_meta: dict[str, Any]) -> WrappedKeyBundle:
        key = self._derive_master_key(master_password, kdf_meta)
        nonce = os.urandom(12)
        wrapped = AESGCM(key).encrypt(nonce, dek, b"openclaw-memory-dek")
        return WrappedKeyBundle(
            wrapped_dek=_b64(wrapped),
            wrap_nonce=_b64(nonce),
            kdf=kdf_meta,
        )

    def unwrap_data_key(self, wrapped_bundle: dict[str, Any], master_password: str) -> bytes:
        kdf_meta = dict(wrapped_bundle.get("kdf", {}))
        if not kdf_meta:
            raise KeyManagerError("missing_kdf_metadata")
        key = self._derive_master_key(master_password, kdf_meta)
        nonce = _unb64(str(wrapped_bundle["wrap_nonce"]))
        wrapped = _unb64(str(wrapped_bundle["wrapped_dek"]))
        try:
            return AESGCM(key).decrypt(nonce, wrapped, b"openclaw-memory-dek")
        except (TypeError, ValueError) as exc:
            raise KeyManagerError("invalid_master_password") from exc

    def validate_master_password(self, wrapped_bundle: dict[str, Any], master_password: str) -> bool:
        try:
            _ = self.unwrap_data_key(wrapped_bundle, master_password)
            return True
        except KeyManagerError:
            return False

    def generate_recovery_key(self) -> str:
        raw = os.urandom(18)
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    def _derive_recovery_key(self, recovery_key: str, salt: bytes) -> bytes:
        return hashlib.pbkdf2_hmac("sha256", recovery_key.encode("utf-8"), salt, 150_000, dklen=32)

    def create_recovery_material(self, dek: bytes, recovery_key: str) -> dict[str, Any]:
        salt = os.urandom(16)
        nonce = os.urandom(12)
        key = self._derive_recovery_key(recovery_key, salt)
        wrapped = AESGCM(key).encrypt(nonce, dek, b"openclaw-memory-recovery")
        checksum = _b64(hmac.new(key, b"openclaw-memory-recovery-check", hashlib.sha256).digest())
        return {
            "version": 1,
            "salt": _b64(salt),
            "nonce": _b64(nonce),
            "wrapped_dek": _b64(wrapped),
            "checksum": checksum,
        }

    def recover_data_key(self, recovery_material: dict[str, Any], recovery_key: str) -> bytes:
        salt = _unb64(str(recovery_material["salt"]))
        nonce = _unb64(str(recovery_material["nonce"]))
        wrapped = _unb64(str(recovery_material["wrapped_dek"]))
        key = self._derive_recovery_key(recovery_key, salt)
        try:
            return AESGCM(key).decrypt(nonce, wrapped, b"openclaw-memory-recovery")
        except (TypeError, ValueError) as exc:
            raise KeyManagerError("invalid_recovery_key") from exc

    def save_material(self, wrapped_bundle: WrappedKeyBundle, enabled: bool = True) -> None:
        payload = {
            "enabled": bool(enabled),
            "wrapped_dek": wrapped_bundle.wrapped_dek,
            "wrap_nonce": wrapped_bundle.wrap_nonce,
            "kdf": wrapped_bundle.kdf,
        }
        if self._keychain_available():
            self._keychain_set_material(payload)
            mirror = {
                "enabled": bool(enabled),
                "backend": "keychain",
                "kdf": wrapped_bundle.kdf,
                "service": self.keychain_service,
                "account": self.keychain_account,
            }
            self.key_material_path.write_text(json.dumps(mirror, ensure_ascii=False, indent=2), encoding="utf-8")
            return
        self.key_material_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_material(self) -> dict[str, Any]:
        if self._keychain_available():
            try:
                payload = self._keychain_get_material()
                if isinstance(payload, dict) and payload.get("wrapped_dek") and payload.get("kdf"):
                    return payload
            except KeyManagerError as exc:
                print(f"[KeyManager] Keychain material read failed, falling back to file mirror: {exc}")
        if not self.key_material_path.exists():
            return {}
        return json.loads(self.key_material_path.read_text(encoding="utf-8") or "{}")

    def save_recovery_material(self, payload: dict[str, Any]) -> None:
        self.recovery_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_recovery_material(self) -> dict[str, Any]:
        if not self.recovery_path.exists():
            return {}
        return json.loads(self.recovery_path.read_text(encoding="utf-8") or "{}")

    @staticmethod
    def password_fingerprint(master_password: str, salt: bytes) -> str:
        return _b64(hashlib.pbkdf2_hmac("sha256", master_password.encode("utf-8"), salt, 50_000, dklen=16))
