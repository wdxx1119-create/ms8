"""Explicit runtime-format selection for the isolated ledger-v1 boundary.

The environment flag is an enablement gate only. It never performs migration or
changes the active format by itself; the persisted manifest remains authoritative
for runtime-format selection.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

RUNTIME_FORMAT_SCHEMA = "ms8.runtime-format.v1"
LEGACY_RUNTIME_FORMAT = "legacy-records-v1"
LEDGER_V1_RUNTIME_FORMAT = "ledger-v1"
LEDGER_V1_ENV_FLAG = "MS8_MEMORY_LEDGER_V1"
_ALLOWED_FORMATS = {LEGACY_RUNTIME_FORMAT, LEDGER_V1_RUNTIME_FORMAT}
_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
_HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


class RuntimeFormatManifestError(ValueError):
    """Raised when runtime-format metadata is missing required invariants."""


def _require_text(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise RuntimeFormatManifestError(f"{field_name} must not be empty")
    return text


def _validate_timestamp(value: object, field_name: str) -> str:
    text = _require_text(value, field_name)
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise RuntimeFormatManifestError(f"{field_name} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise RuntimeFormatManifestError(f"{field_name} must include a timezone")
    return text


@dataclass(frozen=True, slots=True)
class RuntimeFormatManifest:
    schema: str
    active_format: str
    generation: int
    updated_at: str
    previous_format: str | None = None
    migration_id: str | None = None
    ledger_head: str | None = None

    def __post_init__(self) -> None:
        if self.schema != RUNTIME_FORMAT_SCHEMA:
            raise RuntimeFormatManifestError(f"unsupported runtime-format schema: {self.schema}")
        if self.active_format not in _ALLOWED_FORMATS:
            raise RuntimeFormatManifestError(f"unsupported active_format: {self.active_format}")
        if isinstance(self.generation, bool) or not isinstance(self.generation, int) or self.generation < 0:
            raise RuntimeFormatManifestError("generation must be a non-negative integer")
        _validate_timestamp(self.updated_at, "updated_at")
        if self.previous_format is not None and self.previous_format not in _ALLOWED_FORMATS:
            raise RuntimeFormatManifestError(f"unsupported previous_format: {self.previous_format}")
        if self.active_format == LEDGER_V1_RUNTIME_FORMAT:
            if self.generation < 1:
                raise RuntimeFormatManifestError("ledger-v1 manifest requires generation >= 1")
            migration_id = _require_text(self.migration_id, "migration_id")
            ledger_head = _require_text(self.ledger_head, "ledger_head")
            if _HASH_PATTERN.fullmatch(ledger_head) is None:
                raise RuntimeFormatManifestError("ledger_head must use sha256:<64 lowercase hex>")
            object.__setattr__(self, "migration_id", migration_id)
            object.__setattr__(self, "ledger_head", ledger_head)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "active_format": self.active_format,
            "generation": self.generation,
            "updated_at": self.updated_at,
            "previous_format": self.previous_format,
            "migration_id": self.migration_id,
            "ledger_head": self.ledger_head,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> RuntimeFormatManifest:
        previous = payload.get("previous_format")
        migration_id = payload.get("migration_id")
        ledger_head = payload.get("ledger_head")
        generation = payload.get("generation")
        if isinstance(generation, bool) or not isinstance(generation, int):
            raise RuntimeFormatManifestError("generation must be a non-negative integer")
        return cls(
            schema=str(payload.get("schema") or ""),
            active_format=str(payload.get("active_format") or ""),
            generation=generation,
            updated_at=str(payload.get("updated_at") or ""),
            previous_format=str(previous) if previous not in (None, "") else None,
            migration_id=str(migration_id) if migration_id not in (None, "") else None,
            ledger_head=str(ledger_head) if ledger_head not in (None, "") else None,
        )


@dataclass(frozen=True, slots=True)
class RuntimeFormatDecision:
    selected_format: str
    allowed: bool
    reason: str
    ledger_v1_flag_enabled: bool
    manifest_generation: int


def default_runtime_format_manifest() -> RuntimeFormatManifest:
    """Return the in-memory legacy default without writing or switching anything."""

    return RuntimeFormatManifest(
        schema=RUNTIME_FORMAT_SCHEMA,
        active_format=LEGACY_RUNTIME_FORMAT,
        generation=0,
        updated_at="1970-01-01T00:00:00+00:00",
    )


def load_runtime_format_manifest(path: Path) -> RuntimeFormatManifest:
    """Load explicit runtime metadata; a missing file means legacy default."""

    manifest_path = Path(path)
    if not manifest_path.is_file():
        return default_runtime_format_manifest()
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeFormatManifestError("runtime-format manifest is unreadable") from exc
    if not isinstance(payload, Mapping):
        raise RuntimeFormatManifestError("runtime-format manifest must be a JSON object")
    return RuntimeFormatManifest.from_dict(payload)


def ledger_v1_flag_enabled(environ: Mapping[str, str] | None = None) -> bool:
    values = os.environ if environ is None else environ
    return str(values.get(LEDGER_V1_ENV_FLAG, "")).strip().casefold() in _TRUE_VALUES


def evaluate_runtime_format(
    manifest: RuntimeFormatManifest,
    environ: Mapping[str, str] | None = None,
) -> RuntimeFormatDecision:
    """Evaluate the explicit manifest and fail closed for an unarmed ledger format."""

    flag_enabled = ledger_v1_flag_enabled(environ)
    if manifest.active_format == LEGACY_RUNTIME_FORMAT:
        reason = "legacy_manifest_active"
        if flag_enabled:
            reason = "ledger_v1_flag_armed_but_manifest_remains_legacy"
        return RuntimeFormatDecision(
            selected_format=LEGACY_RUNTIME_FORMAT,
            allowed=True,
            reason=reason,
            ledger_v1_flag_enabled=flag_enabled,
            manifest_generation=manifest.generation,
        )
    if not flag_enabled:
        return RuntimeFormatDecision(
            selected_format=LEDGER_V1_RUNTIME_FORMAT,
            allowed=False,
            reason="ledger_v1_flag_required",
            ledger_v1_flag_enabled=False,
            manifest_generation=manifest.generation,
        )
    return RuntimeFormatDecision(
        selected_format=LEDGER_V1_RUNTIME_FORMAT,
        allowed=True,
        reason="ledger_v1_manifest_and_flag_enabled",
        ledger_v1_flag_enabled=True,
        manifest_generation=manifest.generation,
    )


__all__ = [
    "LEDGER_V1_ENV_FLAG",
    "LEDGER_V1_RUNTIME_FORMAT",
    "LEGACY_RUNTIME_FORMAT",
    "RUNTIME_FORMAT_SCHEMA",
    "RuntimeFormatDecision",
    "RuntimeFormatManifest",
    "RuntimeFormatManifestError",
    "default_runtime_format_manifest",
    "evaluate_runtime_format",
    "ledger_v1_flag_enabled",
    "load_runtime_format_manifest",
]
