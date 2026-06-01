"""Policy license validation (Phase 1)."""

from __future__ import annotations

import json
import os
from binascii import Error as BinasciiError
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from ..paths import get_config_dir


@dataclass
class PolicyLicenseStatus:
    status: str
    reason_code: str
    enabled: bool
    strict_mode: bool
    license_path: str
    subject: str = ""
    days_to_expiry: int | None = None
    grace_days_left: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _is_enabled() -> bool:
    val = str(os.getenv("MS8_POLICY_LICENSE_ENABLED", "0")).strip().lower()
    return val in {"1", "true", "yes", "on"}


def _is_strict_mode() -> bool:
    val = str(os.getenv("MS8_POLICY_LICENSE_STRICT", "0")).strip().lower()
    return val in {"1", "true", "yes", "on"}


def _license_path() -> Path:
    return get_config_dir() / "policy_license"


def _grace_days() -> int:
    raw = str(os.getenv("MS8_POLICY_LICENSE_GRACE_DAYS", "7")).strip()
    try:
        days = int(raw)
    except ValueError:
        return 7
    return max(0, days)


def _public_key_pem() -> str:
    # Default empty -> signature verification disabled unless explicitly configured.
    return str(os.getenv("MS8_POLICY_LICENSE_PUBKEY_PEM", "")).strip()


def _device_id() -> str:
    return str(os.getenv("MS8_POLICY_DEVICE_ID", "")).strip()


def _now_ts() -> int:
    raw = str(os.getenv("MS8_POLICY_LICENSE_NOW_TS", "")).strip()
    if raw:
        try:
            return int(raw)
        except ValueError:
            # Ignore invalid override and fall back to wall clock time.
            raw = ""
    import time

    return int(time.time())


def _normalize_payload_for_signing(raw: dict[str, Any]) -> bytes:
    payload = {k: raw[k] for k in raw.keys() if k != "sig"}
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return text.encode("utf-8")


def _verify_signature(raw: dict[str, Any], pubkey_pem: str) -> tuple[bool, str]:
    sig = raw.get("sig", "")
    if not isinstance(sig, str) or not sig.strip():
        return False, "license_signature_missing"
    try:
        import base64

        sig_bytes = base64.b64decode(sig.encode("utf-8"))
    except (BinasciiError, ValueError):
        return False, "license_signature_invalid_base64"
    try:
        pub = serialization.load_pem_public_key(pubkey_pem.encode("utf-8"))
    except (TypeError, ValueError, UnsupportedAlgorithm):
        return False, "license_pubkey_invalid"
    if not isinstance(pub, Ed25519PublicKey):
        return False, "license_pubkey_not_ed25519"
    try:
        pub.verify(sig_bytes, _normalize_payload_for_signing(raw))
    except (InvalidSignature, ValueError, TypeError):
        return False, "license_signature_invalid"
    return True, "ok"


def validate_policy_license() -> PolicyLicenseStatus:
    """Validate policy license file.

    Phase 0 behavior:
    - default disabled
    - no cryptographic verification yet
    - never blocks loader decisions
    """

    enabled = _is_enabled()
    strict_mode = _is_strict_mode()
    path = _license_path()

    if not enabled:
        return PolicyLicenseStatus(
            status="disabled",
            reason_code="license_check_disabled",
            enabled=enabled,
            strict_mode=strict_mode,
            license_path=str(path),
        )

    if not path.exists():
        return PolicyLicenseStatus(
            status="missing",
            reason_code="license_file_missing",
            enabled=enabled,
            strict_mode=strict_mode,
            license_path=str(path),
        )

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return PolicyLicenseStatus(
            status="invalid",
            reason_code="license_file_invalid_json",
            enabled=enabled,
            strict_mode=strict_mode,
            license_path=str(path),
        )

    pubkey = _public_key_pem()
    if not pubkey:
        return PolicyLicenseStatus(
            status="warn",
            reason_code="license_pubkey_missing",
            enabled=enabled,
            strict_mode=strict_mode,
            license_path=str(path),
            subject=str(raw.get("sub", "")).strip(),
        )

    ok, code = _verify_signature(raw, pubkey)
    if not ok:
        return PolicyLicenseStatus(
            status="invalid",
            reason_code=code,
            enabled=enabled,
            strict_mode=strict_mode,
            license_path=str(path),
            subject=str(raw.get("sub", "")).strip(),
        )

    now = _now_ts()
    exp = raw.get("exp", 0)
    exp_i = int(exp) if isinstance(exp, (int, float, str)) and str(exp).strip() else 0
    grace_days = _grace_days()
    grace_secs = grace_days * 86400
    if exp_i > 0:
        delta = exp_i - now
        if delta >= 0:
            return PolicyLicenseStatus(
                status="valid",
                reason_code="license_valid",
                enabled=enabled,
                strict_mode=strict_mode,
                license_path=str(path),
                subject=str(raw.get("sub", "")).strip(),
                days_to_expiry=max(0, delta // 86400),
            )
        if abs(delta) <= grace_secs:
            return PolicyLicenseStatus(
                status="grace",
                reason_code="license_in_grace_period",
                enabled=enabled,
                strict_mode=strict_mode,
                license_path=str(path),
                subject=str(raw.get("sub", "")).strip(),
                grace_days_left=max(0, (grace_secs - abs(delta)) // 86400),
            )
        return PolicyLicenseStatus(
            status="invalid",
            reason_code="license_expired",
            enabled=enabled,
            strict_mode=strict_mode,
            license_path=str(path),
            subject=str(raw.get("sub", "")).strip(),
        )

    devices = raw.get("devices", [])
    if isinstance(devices, list) and devices:
        local_device = _device_id()
        allowed = {str(x).strip() for x in devices if str(x).strip()}
        if local_device and local_device not in allowed:
            return PolicyLicenseStatus(
                status="invalid",
                reason_code="license_device_mismatch",
                enabled=enabled,
                strict_mode=strict_mode,
                license_path=str(path),
                subject=str(raw.get("sub", "")).strip(),
            )

    return PolicyLicenseStatus(
        status="valid",
        reason_code="license_valid",
        enabled=enabled,
        strict_mode=strict_mode,
        license_path=str(path),
        subject=str(raw.get("sub", "")).strip(),
    )
