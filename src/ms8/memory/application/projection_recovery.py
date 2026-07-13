"""Guarded deterministic rebuild service for disposable ledger-v1 projections.

The authoritative JSONL ledger is never rewritten by this service. A dry-run is the
normal default. Applying a rebuild requires the caller to provide the current ledger
head and an exact confirmation token. Projection adapters remain responsible for
atomic artifact replacement.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.ledger import GENESIS_HASH
from ..ports.record_store import HeadMismatchError, LedgerIntegrityError
from .projection_service import ProjectionCoordinator, ProjectionSetStatus


class ProjectionRecoveryError(RuntimeError):
    """Raised when projection recovery authorization or invariants fail."""


@dataclass(frozen=True, slots=True)
class ProjectionRebuildPreview:
    ledger_head: str
    last_sequence: int
    logical_state_hash: str | None
    ready_before: bool
    reason_codes: tuple[str, ...]
    projection_states: tuple[dict[str, object], ...]
    required_confirmation: str

    def to_dict(self) -> dict[str, object]:
        return {
            "ledger_head": self.ledger_head,
            "last_sequence": self.last_sequence,
            "logical_state_hash": self.logical_state_hash,
            "ready_before": self.ready_before,
            "reason_codes": list(self.reason_codes),
            "projection_states": [dict(item) for item in self.projection_states],
            "required_confirmation": self.required_confirmation,
        }


@dataclass(frozen=True, slots=True)
class ProjectionRebuildResult:
    applied: bool
    ledger_head: str
    last_sequence: int
    logical_state_hash: str | None
    ready_before: bool
    ready_after: bool
    rebuilt_projections: tuple[str, ...]
    reason_codes_before: tuple[str, ...]
    reason_codes_after: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "applied": self.applied,
            "ledger_head": self.ledger_head,
            "last_sequence": self.last_sequence,
            "logical_state_hash": self.logical_state_hash,
            "ready_before": self.ready_before,
            "ready_after": self.ready_after,
            "rebuilt_projections": list(self.rebuilt_projections),
            "reason_codes_before": list(self.reason_codes_before),
            "reason_codes_after": list(self.reason_codes_after),
        }


def _projection_states(status: ProjectionSetStatus) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "name": item.name,
            "exists": item.exists,
            "fresh": item.fresh,
            "projection_head": item.projection_head,
            "ledger_head": item.ledger_head,
            "reason": item.reason,
            "logical_state_hash": item.logical_state_hash,
        }
        for item in status.freshness
    )


class ProjectionRecoveryService:
    """Preview or atomically rebuild every configured projection from the ledger."""

    def __init__(self, coordinator: ProjectionCoordinator):
        self.coordinator = coordinator

    def _verified_head(self) -> tuple[str, int]:
        verification = self.coordinator.record_store.verify()
        if not verification.valid:
            raise LedgerIntegrityError(
                "cannot rebuild projections from invalid ledger: "
                + ",".join(verification.reason_codes)
            )
        return verification.last_valid_hash or GENESIS_HASH, verification.last_sequence or 0

    @staticmethod
    def confirmation_token(ledger_head: str) -> str:
        normalized = str(ledger_head or "").strip()
        if not normalized:
            raise ProjectionRecoveryError("ledger_head is required for rebuild confirmation")
        return f"REBUILD_PROJECTIONS:{normalized}"

    def preview(self) -> ProjectionRebuildPreview:
        ledger_head, last_sequence = self._verified_head()
        status = self.coordinator.status()
        if status.ledger_head != ledger_head or status.last_sequence != last_sequence:
            raise LedgerIntegrityError("projection status does not match the verified ledger")
        return ProjectionRebuildPreview(
            ledger_head=ledger_head,
            last_sequence=last_sequence,
            logical_state_hash=status.logical_state_hash,
            ready_before=status.ready_for_query,
            reason_codes=status.reason_codes,
            projection_states=_projection_states(status),
            required_confirmation=self.confirmation_token(ledger_head),
        )

    def rebuild(
        self,
        expected_head_hash: str,
        *,
        apply: bool = False,
        confirmation: str = "",
    ) -> ProjectionRebuildResult:
        expected = str(expected_head_hash or "").strip()
        if not expected:
            raise ProjectionRecoveryError("expected_head_hash is required")

        ledger_head, last_sequence = self._verified_head()
        if expected != ledger_head:
            raise HeadMismatchError(f"expected head {expected}, current head {ledger_head}")

        before = self.coordinator.status()
        if before.ledger_head != ledger_head or before.last_sequence != last_sequence:
            raise LedgerIntegrityError("projection status does not match the verified ledger")

        if not apply:
            return ProjectionRebuildResult(
                applied=False,
                ledger_head=ledger_head,
                last_sequence=last_sequence,
                logical_state_hash=before.logical_state_hash,
                ready_before=before.ready_for_query,
                ready_after=before.ready_for_query,
                rebuilt_projections=(),
                reason_codes_before=before.reason_codes,
                reason_codes_after=before.reason_codes,
            )

        required = self.confirmation_token(ledger_head)
        if confirmation != required:
            raise ProjectionRecoveryError("exact rebuild confirmation token is required")

        build = self.coordinator.rebuild_all()
        after = self.coordinator.require_ready_for_query()
        if build.ledger_head != ledger_head or after.ledger_head != ledger_head:
            raise LedgerIntegrityError("projection rebuild advanced from an unexpected ledger head")
        if build.last_sequence != last_sequence or after.last_sequence != last_sequence:
            raise LedgerIntegrityError("projection rebuild sequence does not match the ledger")
        if build.logical_state_hash != after.logical_state_hash:
            raise LedgerIntegrityError("projection rebuild logical-state hash mismatch")

        rebuilt = tuple(item.descriptor.name for item in build.projections)
        return ProjectionRebuildResult(
            applied=True,
            ledger_head=ledger_head,
            last_sequence=last_sequence,
            logical_state_hash=after.logical_state_hash,
            ready_before=before.ready_for_query,
            ready_after=after.ready_for_query,
            rebuilt_projections=rebuilt,
            reason_codes_before=before.reason_codes,
            reason_codes_after=after.reason_codes,
        )


__all__ = [
    "ProjectionRebuildPreview",
    "ProjectionRebuildResult",
    "ProjectionRecoveryError",
    "ProjectionRecoveryService",
]
