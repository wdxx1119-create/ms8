"""Ports for ledger-v1 infrastructure adapters."""

from .projection import (
    ProjectionAdapter,
    ProjectionBuildResult,
    ProjectionDescriptor,
    ProjectionFreshness,
)
from .record_store import (
    AppendResult,
    HeadMismatchError,
    LedgerIntegrityError,
    LedgerVerification,
    RecordStore,
    RestoreResult,
    SnapshotRef,
    TailRepairResult,
)

__all__ = [
    "AppendResult",
    "HeadMismatchError",
    "LedgerIntegrityError",
    "LedgerVerification",
    "ProjectionAdapter",
    "ProjectionBuildResult",
    "ProjectionDescriptor",
    "ProjectionFreshness",
    "RecordStore",
    "RestoreResult",
    "SnapshotRef",
    "TailRepairResult",
]
