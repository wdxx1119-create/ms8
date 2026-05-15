from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from .file_crypto import decrypt_bytes, encrypt_bytes, is_encrypted_blob
from .key_manager import KeyManager, KeyManagerError
from .security_schema import SecurityConfig, SecurityStatus


class CryptoError(Exception):
    pass


class CryptoLockedError(CryptoError):
    pass


class CryptoManager:
    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config
        memory_cfg = (config.get("settings", {}).get("memory", {}) or {})
        self.security_cfg = SecurityConfig.from_dict(memory_cfg.get("security", {}))
        self.workspace_dir: Path = Path(config["workspace_dir"])
        self.memory_dir: Path = Path(config["memory_dir"])
        self.security_dir = self._resolve(self.security_cfg.security_dir)
        self.security_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self._resolve(self.security_cfg.state_file)
        self.key_material_path = self._resolve(self.security_cfg.key_material_file)
        self.recovery_path = self._resolve(self.security_cfg.recovery_material_file)
        self.km = KeyManager(
            self.key_material_path,
            self.recovery_path,
            use_keychain=bool(self.security_cfg.use_keychain),
            keychain_service=str(self.security_cfg.keychain_service),
            keychain_account=str(self.security_cfg.keychain_account),
        )
        self._dek: Optional[bytes] = None
        self._last_unlock_at: Optional[str] = None
        self._state = self._load_state()

    def _resolve(self, raw: str) -> Path:
        p = Path(str(raw)).expanduser()
        return p if p.is_absolute() else (self.workspace_dir / p)

    def _load_state(self) -> Dict[str, Any]:
        if not self.state_path.exists():
            return {"enabled": bool(self.security_cfg.enabled), "last_unlock_at": None}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8") or "{}")
        except Exception:
            return {"enabled": bool(self.security_cfg.enabled), "last_unlock_at": None}

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8")

    def is_enabled(self) -> bool:
        return bool(self._state.get("enabled", False) or self.security_cfg.enabled)

    def is_unlocked(self) -> bool:
        return bool(self._dek is not None)

    def _target_patterns(self) -> Iterable[str]:
        return self.security_cfg.encrypted_targets or [
            "MEMORY.md",
            "memory/auto_memory_records.jsonl",
            "memory/working_memory.jsonl",
            "memory/memory_blocks.json",
            "memory/auto_memory_index.json",
        ]

    def should_protect_path(self, path: Path) -> bool:
        target_abs = str(path.resolve())
        rel = target_abs.replace(str(self.workspace_dir.resolve()) + "/", "")
        for pattern in self._target_patterns():
            pattern = str(pattern).strip()
            if not pattern:
                continue
            p_obj = Path(pattern).expanduser()
            if p_obj.is_absolute():
                if target_abs == str(p_obj.resolve()):
                    return True
                continue
            if pattern.endswith("/"):
                if rel.startswith(pattern):
                    return True
                continue
            if rel == pattern or rel.endswith(pattern):
                return True
        return False

    def status(self) -> Dict[str, Any]:
        session_state = "disabled"
        if self.is_enabled():
            session_state = "unlocked" if self.is_unlocked() else "locked"
        status = SecurityStatus(
            enabled=self.is_enabled(),
            session_state=session_state,
            protected_targets=list(self._target_patterns()),
            recovery_key_available=self.recovery_path.exists(),
            last_unlock_at=self._last_unlock_at or self._state.get("last_unlock_at"),
        )
        return {
            "enabled": status.enabled,
            "session_state": status.session_state,
            "protected_targets": status.protected_targets,
            "recovery_key_available": status.recovery_key_available,
            "last_unlock_at": status.last_unlock_at,
            "key_backend": "keychain" if bool(self.security_cfg.use_keychain) else "file",
            "keychain_service": str(self.security_cfg.keychain_service),
            "keychain_account": str(self.security_cfg.keychain_account),
        }

    def enable_encryption(self, master_password: str) -> Dict[str, Any]:
        if self.is_enabled():
            return {"status": "skipped", "reason": "already_enabled"}
        kdf_meta = self.km.create_master_secret(master_password)
        dek = self.km.generate_data_key()
        wrapped = self.km.wrap_data_key(dek, master_password, kdf_meta)
        recovery_key = self.km.generate_recovery_key()
        recovery = self.km.create_recovery_material(dek, recovery_key)
        self.km.save_material(wrapped, enabled=True)
        self.km.save_recovery_material(recovery)
        self._dek = dek
        self._state["enabled"] = True
        self._state["last_unlock_at"] = datetime.now(timezone.utc).isoformat()
        self._save_state()
        migrated = self.migrate_plaintext_targets()
        return {
            "status": "success",
            "recovery_key": recovery_key,
            "migrated": migrated,
            "status_view": self.status(),
        }

    def disable_encryption(self, master_password: str) -> Dict[str, Any]:
        if not self.is_enabled():
            return {"status": "skipped", "reason": "already_disabled"}
        if not self.unlock(master_password):
            return {"status": "error", "reason": "invalid_master_password"}
        migrated = self.decrypt_targets_to_plaintext()
        self._dek = None
        self._state["enabled"] = False
        self._state["last_unlock_at"] = None
        self._save_state()
        return {"status": "success", "migrated": migrated, "status_view": self.status()}

    def unlock(self, master_password: str) -> bool:
        material = self.km.load_material()
        if not material:
            raise CryptoError("key_material_missing")
        try:
            self._dek = self.km.unwrap_data_key(material, master_password)
            self._last_unlock_at = datetime.now(timezone.utc).isoformat()
            self._state["last_unlock_at"] = self._last_unlock_at
            self._save_state()
            return True
        except KeyManagerError:
            self._dek = None
            return False

    def lock(self) -> None:
        self._dek = None

    def _ensure_unlocked(self) -> None:
        if self.is_enabled() and not self.is_unlocked():
            raise CryptoLockedError("memory_security_locked")

    def encrypt_before_write(self, data: bytes, file_type: str, target_path: Optional[Path] = None) -> bytes:
        if not self.is_enabled():
            return data
        path = Path(target_path) if target_path else None
        if path is not None and not self.should_protect_path(path):
            return data
        self._ensure_unlocked()
        return encrypt_bytes(data, file_type=file_type, dek=self._dek or b"", kdf=self.km.load_material().get("kdf", {}).get("kdf", "argon2id"))

    def decrypt_after_read(self, blob: bytes, target_path: Optional[Path] = None, allow_plaintext: bool = True) -> bytes:
        if not self.is_enabled():
            return blob
        path = Path(target_path) if target_path else None
        if path is not None and not self.should_protect_path(path):
            return blob
        if is_encrypted_blob(blob):
            self._ensure_unlocked()
            return decrypt_bytes(blob, self._dek or b"")
        if allow_plaintext:
            # Backward compatibility for pre-migration files.
            return blob
        raise CryptoError("plaintext_not_allowed_for_protected_file")

    def migrate_plaintext_targets(self) -> Dict[str, Any]:
        if not self.is_enabled():
            return {"status": "skipped", "reason": "disabled", "converted": []}
        self._ensure_unlocked()
        converted = []
        skipped = []
        for rel in self._target_patterns():
            target = self._resolve(rel)
            if not target.exists() or target.is_dir():
                skipped.append(str(target))
                continue
            raw = target.read_bytes()
            if is_encrypted_blob(raw):
                skipped.append(str(target))
                continue
            encrypted = self.encrypt_before_write(raw, file_type=self._guess_file_type(target), target_path=target)
            tmp = target.with_suffix(target.suffix + ".enc.tmp")
            tmp.write_bytes(encrypted)
            tmp.replace(target)
            converted.append(str(target))
        return {"status": "success", "converted": converted, "skipped": skipped}

    def decrypt_targets_to_plaintext(self) -> Dict[str, Any]:
        if not self.is_enabled():
            return {"status": "skipped", "reason": "disabled", "converted": []}
        self._ensure_unlocked()
        converted = []
        skipped = []
        for rel in self._target_patterns():
            target = self._resolve(rel)
            if not target.exists() or target.is_dir():
                skipped.append(str(target))
                continue
            raw = target.read_bytes()
            if not is_encrypted_blob(raw):
                skipped.append(str(target))
                continue
            plain = self.decrypt_after_read(raw, target_path=target, allow_plaintext=True)
            tmp = target.with_suffix(target.suffix + ".dec.tmp")
            tmp.write_bytes(plain)
            tmp.replace(target)
            converted.append(str(target))
        return {"status": "success", "converted": converted, "skipped": skipped}

    def _guess_file_type(self, path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix in {".md", ".txt"}:
            return "text"
        if suffix in {".json", ".jsonl"}:
            return "json"
        if suffix in {".log"}:
            return "log"
        if suffix in {".db", ".sqlite"}:
            return "sqlite"
        return "binary"


_CRYPTO_CACHE: Dict[int, CryptoManager] = {}


def get_crypto_manager(config: Dict[str, Any]) -> CryptoManager:
    key = hash(str(Path(config.get("workspace_dir", ".")).expanduser().resolve()))
    mgr = _CRYPTO_CACHE.get(key)
    if mgr is None:
        mgr = CryptoManager(config)
        _CRYPTO_CACHE[key] = mgr
    return mgr
