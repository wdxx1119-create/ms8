from __future__ import annotations

from pathlib import Path
from typing import Any

from ..record_policy import (
    append_canonical_record,
    repair_scope_flags,
    validate_file_and_quarantine,
)


def append_memory_record(
    *,
    memory_dir: Path,
    text: str,
    source: str,
    status: str = "accepted",
) -> dict[str, Any]:
    """Internal-only low-level append helper.

    Prefer `MemoryCore.write_gateway()` for all application write paths so
    admission/review/audit rules remain centralized.
    """
    records_file = memory_dir / "auto_memory_records.jsonl"
    quarantine_file = memory_dir / "noncanonical_quarantine.jsonl"
    row, _ok, _reason = append_canonical_record(
        records_file=records_file,
        quarantine_file=quarantine_file,
        text=text,
        source=source,
        status=status,
    )
    return row


def normalize_memory_records(memory_dir: Path) -> dict[str, Any]:
    records_file = memory_dir / "auto_memory_records.jsonl"
    quarantine_file = memory_dir / "noncanonical_quarantine.jsonl"
    validate_file_and_quarantine(records_file, quarantine_file)
    stats = repair_scope_flags(records_file)
    return {
        "records_file": str(records_file),
        "quarantine_file": str(quarantine_file),
        **stats,
    }
