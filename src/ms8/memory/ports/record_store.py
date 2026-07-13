"""Authoritative ledger storage port.

Infrastructure implementations must provide durable append, optimistic head
checks, complete-chain verification, snapshots, controlled tail repair, and
verified restore. The port deliberately contains no lifecycle, ranking, or
policy logic.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from ..domain.ledger import LedgerTransaction


class HeadMismatchError(RuntimeError):
    """Raised when an optimistic ``expected_head`` precondition fails."""


class LedgerIntegrityError(RuntimeError):
    """Raised when malformed or hash-broken ledger data is encountered."""


@dataclass(frozen=True, slots=True)
class AppendResult:
    transaction_id: str
    sequence: int
    previous_head: str
    new_head: str
    ledger_path: Path
    durable: bool


@dataclass(frozen=True, slots=True)
class LedgerVerification:
    valid: bool
    transaction_count: int
    first_sequence: int | None
    last_sequence: int | None
    last_valid_hash: str | None
    invalid_sequence: int | None
    reason_codes: tuple[str, ...]
    truncated_tail_detected: bool = False
    invalid_line_number: int | None = None
    valid_bytes: int = 0
    total_bytes: int = 0
    damaged_bytes: int = 0
    repairable_tail: bool = False


@dataclass(frozen=True, slots=True)
class SnapshotRef:
    snapshot_id: str
    path: Path
    ledger_head: str
    last_sequence: int
    created_at: str
    manifest_hash: str


@dataclass(frozen=True, slots=True)
class TailRepairResult:
    applied: bool
    repairable: bool
    removed_bytes: int
    backup_path: Path | None
    previous_reason_codes: tuple[str, ...]
    last_valid_sequence: int
    last_valid_hash: str


@dataclass(frozen=True, slots=True)
class RestoreResult:
    applied: bool
    snapshot_path: Path
    previous_head: str
    restored_head: str
    restored_sequence: int
    pre_restore_backup: Path | None


@runtime_checkable
class RecordStore(Protocol):
    """Persistence boundary for the single authoritative ledger."""

    def append(self, transaction: LedgerTransaction, expected_head: str | None = None) -> AppendResult:
        """Durably append one complete transaction or fail without advancing the head."""

    def iterate(self, after_sequence: int = 0) -> Iterator[LedgerTransaction]:
        """Yield only complete, valid transactions after ``after_sequence``."""

    def verify(self) -> LedgerVerification:
        """Verify schema, sequence continuity, hashes, and tail completeness."""

    def snapshot(self) -> SnapshotRef:
        """Create a verified snapshot bound to the current ledger head."""

    def export_snapshot(self, snapshot: SnapshotRef, destination: Path) -> SnapshotRef:
        """Copy a verified snapshot to an explicit external destination."""

    def repair_tail(self, *, dry_run: bool = True) -> TailRepairResult:
        """Inspect or remove only a repairable damaged final ledger record."""

    def restore_snapshot(
        self,
        snapshot_path: Path,
        *,
        expected_head: str | None = None,
        dry_run: bool = True,
    ) -> RestoreResult:
        """Inspect or restore a verified snapshot with a pre-restore backup."""
