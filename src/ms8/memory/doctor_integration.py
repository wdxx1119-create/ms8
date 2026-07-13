"""Read-only integration surface for the main MS8 doctor command."""

from __future__ import annotations

from pathlib import Path

from .operations_cli import _doctor
from .runtime_format import LEDGER_V1_RUNTIME_FORMAT, load_runtime_format_manifest


def ledger_doctor_status(workspace: Path) -> dict[str, object]:
    """Inspect ledger-v1 only when its runtime manifest is active.

    A legacy or missing manifest returns an inactive result without constructing a
    ledger store or creating ledger directories.
    """

    root = Path(workspace).expanduser().resolve()
    manifest_path = root / "memory" / "runtime-format.json"
    manifest = load_runtime_format_manifest(manifest_path)
    if manifest.active_format != LEDGER_V1_RUNTIME_FORMAT:
        return {
            "ok": True,
            "status": "inactive",
            "active_format": manifest.active_format,
            "manifest_generation": manifest.generation,
            "reason_codes": ["legacy_runtime_active"],
            "read_only": True,
        }
    return _doctor(root)


__all__ = ["ledger_doctor_status"]
