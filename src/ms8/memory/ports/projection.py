"""Generic contracts for disposable ledger-derived projections."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypeVar, runtime_checkable

ProjectionSourceT = TypeVar("ProjectionSourceT", contravariant=True)


@dataclass(frozen=True, slots=True)
class ProjectionDescriptor:
    """Stable metadata shared by every disposable projection."""

    name: str
    schema: str
    artifact_path: Path
    built_from_ledger_head: str
    last_sequence: int
    logical_state_hash: str
    builder_version: str
    artifact_hash: str


@dataclass(frozen=True, slots=True)
class ProjectionBuildResult:
    """Result of atomically rebuilding one projection artifact."""

    descriptor: ProjectionDescriptor
    replaced_existing: bool


@dataclass(frozen=True, slots=True)
class ProjectionFreshness:
    """Whether a projection is present, readable, and bound to the ledger head."""

    name: str
    exists: bool
    fresh: bool
    projection_head: str | None
    ledger_head: str
    reason: str
    logical_state_hash: str | None = None


@runtime_checkable
class ProjectionAdapter(Protocol[ProjectionSourceT]):
    """Build and inspect a projection without mutating authoritative state."""

    @property
    def name(self) -> str:
        """Return the stable projection name."""

    def rebuild_from_state(self, source: ProjectionSourceT) -> ProjectionBuildResult:
        """Atomically rebuild from already-verified replay state."""

    def read_descriptor(self) -> ProjectionDescriptor | None:
        """Read projection metadata, returning ``None`` for missing/invalid data."""

    def freshness(self, ledger_head: str) -> ProjectionFreshness:
        """Compare the projection descriptor with the current ledger head."""
