from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class SecurityStatus:
    enabled: bool
    session_state: str
    protected_targets: List[str] = field(default_factory=list)
    recovery_key_available: bool = False
    last_unlock_at: Optional[str] = None


@dataclass
class EncryptionMetadata:
    version: str
    cipher: str
    kdf: str
    nonce: str
    file_type: str
    created_at: str


@dataclass
class SecurityConfig:
    enabled: bool = False
    encrypted_targets: List[str] = field(default_factory=list)
    session_cache_enabled: bool = True
    require_unlock_for_maintenance: bool = True
    security_dir: str = "memory/security"
    key_material_file: str = "memory/security/key_material.json"
    recovery_material_file: str = "memory/security/recovery_material.json"
    state_file: str = "memory/security/security_state.json"
    use_keychain: bool = False
    keychain_service: str = "openclaw-memory"
    keychain_account: str = "master-key"

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "SecurityConfig":
        data = payload or {}
        return cls(
            enabled=bool(data.get("enabled", False)),
            encrypted_targets=[str(x) for x in data.get("encrypted_targets", [])],
            session_cache_enabled=bool(data.get("session_cache_enabled", True)),
            require_unlock_for_maintenance=bool(data.get("require_unlock_for_maintenance", True)),
            security_dir=str(data.get("security_dir", "memory/security")),
            key_material_file=str(data.get("key_material_file", "memory/security/key_material.json")),
            recovery_material_file=str(data.get("recovery_material_file", "memory/security/recovery_material.json")),
            state_file=str(data.get("state_file", "memory/security/security_state.json")),
            use_keychain=bool(data.get("use_keychain", False)),
            keychain_service=str(data.get("keychain_service", "openclaw-memory")),
            keychain_account=str(data.get("keychain_account", "master-key")),
        )
