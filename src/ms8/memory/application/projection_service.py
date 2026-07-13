"""Unified rebuild, freshness, and query-readiness service for projections."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ..domain.ledger import GENESIS_HASH
from ..ports.projection import (
    ProjectionAdapter,
    ProjectionBuildResult,
    ProjectionFreshness,
)
from ..ports.record_store import LedgerIntegrityError, RecordStore
from .replay import ReplayIntegrityError, ReplayState, replay_transactions


class ProjectionNotReadyError(RuntimeError):
    """Raised when a query attempts to consume missing or stale projections."""


@dataclass(frozen=True, slots=True)
class ProjectionSetBuildResult:
    ledger_head: str
    last_sequence: int
    logical_state_hash: str
    projections: tuple[ProjectionBuildResult, ...]


@dataclass(frozen=True, slots=True)
class ProjectionSetStatus:
    ledger_valid: bool
    ledger_head: str
    last_sequence: int
    logical_state_hash: str | None
    ready_for_query: bool
    freshness: tuple[ProjectionFreshness, ...]
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "ledger_valid": self.ledger_valid,
            "ledger_head": self.ledger_head,
            "last_sequence": self.last_sequence,
            "logical_state_hash": self.logical_state_hash,
            "ready_for_query": self.ready_for_query,
            "reason_codes": list(self.reason_codes),
            "projections": [
                {
                    "name": item.name,
                    "exists": item.exists,
                    "fresh": item.fresh,
                    "projection_head": item.projection_head,
                    "ledger_head": item.ledger_head,
                    "reason": item.reason,
                    "logical_state_hash": item.logical_state_hash,
                }
                for item in self.freshness
            ],
        }


class ProjectionCoordinator:
    """Coordinate projections without granting them authority over the ledger."""

    def __init__(
        self,
        record_store: RecordStore,
        adapters: Iterable[ProjectionAdapter[ReplayState]],
    ):
        self.record_store = record_store
        self.adapters = tuple(adapters)
        names = [adapter.name for adapter in self.adapters]
        if not names:
            raise ValueError("at least one projection adapter is required")
        if len(set(names)) != len(names):
            raise ValueError("projection adapter names must be unique")

    def _verified_state(self) -> ReplayState:
        verification = self.record_store.verify()
        if not verification.valid:
            raise LedgerIntegrityError(
                "cannot consume invalid ledger: " + ",".join(verification.reason_codes)
            )
        state = replay_transactions(self.record_store.iterate())
        expected_head = verification.last_valid_hash or GENESIS_HASH
        expected_sequence = verification.last_sequence or 0
        if state.ledger_head != expected_head or state.last_sequence != expected_sequence:
            raise LedgerIntegrityError("replay state does not match verified ledger head")
        return state

    def rebuild_all(self) -> ProjectionSetBuildResult:
        """Replay once, then atomically replace each independent projection."""

        state = self._verified_state()
        results: list[ProjectionBuildResult] = []
        for adapter in self.adapters:
            result = adapter.rebuild_from_state(state)
            descriptor = result.descriptor
            if descriptor.name != adapter.name:
                raise LedgerIntegrityError(
                    f"projection {adapter.name} returned descriptor for {descriptor.name}"
                )
            if descriptor.built_from_ledger_head != state.ledger_head:
                raise LedgerIntegrityError(f"projection {adapter.name} built from wrong ledger head")
            if descriptor.last_sequence != state.last_sequence:
                raise LedgerIntegrityError(f"projection {adapter.name} built from wrong sequence")
            if descriptor.logical_state_hash != state.logical_state_hash:
                raise LedgerIntegrityError(f"projection {adapter.name} logical state mismatch")
            results.append(result)
        return ProjectionSetBuildResult(
            ledger_head=state.ledger_head,
            last_sequence=state.last_sequence,
            logical_state_hash=state.logical_state_hash,
            projections=tuple(results),
        )

    def status(self) -> ProjectionSetStatus:
        """Return the isolated status contract intended for doctor/query callers."""

        verification = self.record_store.verify()
        ledger_head = verification.last_valid_hash or GENESIS_HASH
        last_sequence = verification.last_sequence or 0
        freshness = tuple(adapter.freshness(ledger_head) for adapter in self.adapters)
        if not verification.valid:
            return ProjectionSetStatus(
                ledger_valid=False,
                ledger_head=ledger_head,
                last_sequence=last_sequence,
                logical_state_hash=None,
                ready_for_query=False,
                freshness=freshness,
                reason_codes=("ledger_invalid", *verification.reason_codes),
            )

        try:
            state = replay_transactions(self.record_store.iterate())
        except ReplayIntegrityError as exc:
            return ProjectionSetStatus(
                ledger_valid=False,
                ledger_head=ledger_head,
                last_sequence=last_sequence,
                logical_state_hash=None,
                ready_for_query=False,
                freshness=freshness,
                reason_codes=("replay_invalid", str(exc)),
            )
        if state.ledger_head != ledger_head or state.last_sequence != last_sequence:
            return ProjectionSetStatus(
                ledger_valid=False,
                ledger_head=ledger_head,
                last_sequence=last_sequence,
                logical_state_hash=state.logical_state_hash,
                ready_for_query=False,
                freshness=freshness,
                reason_codes=("replay_head_mismatch",),
            )

        reasons: list[str] = []
        for item in freshness:
            if not item.fresh:
                reasons.append(f"{item.name}:{item.reason}")
            elif item.logical_state_hash != state.logical_state_hash:
                reasons.append(f"{item.name}:logical_state_mismatch")
        return ProjectionSetStatus(
            ledger_valid=True,
            ledger_head=ledger_head,
            last_sequence=last_sequence,
            logical_state_hash=state.logical_state_hash,
            ready_for_query=not reasons,
            freshness=freshness,
            reason_codes=tuple(reasons),
        )

    def require_ready_for_query(self) -> ProjectionSetStatus:
        """Fail closed when any required projection is missing, stale, or divergent."""

        status = self.status()
        if not status.ready_for_query:
            reasons = ",".join(status.reason_codes) or "projection_not_ready"
            raise ProjectionNotReadyError(reasons)
        return status


__all__ = [
    "ProjectionCoordinator",
    "ProjectionNotReadyError",
    "ProjectionSetBuildResult",
    "ProjectionSetStatus",
]
