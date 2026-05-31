"""Policy license validation skeleton (Phase 0, non-blocking by default)."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

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

    subject = str(raw.get("sub", "")).strip()
    return PolicyLicenseStatus(
        status="warn",
        reason_code="license_phase0_no_signature_verification",
        enabled=enabled,
        strict_mode=strict_mode,
        license_path=str(path),
        subject=subject,
    )

