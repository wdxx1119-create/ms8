"""Detect and durably record first-class conflict events."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from ..domain.ledger import GENESIS_HASH, LedgerEvent, LedgerTransaction
from ..domain.models import Actor
from ..ports.record_store import HeadMismatchError, LedgerIntegrityError, RecordStore
from .conflicts import ConflictCandidate, detect_conflicts
from .replay import replay_transactions


class ConflictRecordingError(ValueError):
    """Raised when conflict recording preconditions are not satisfied."""


@dataclass(frozen=True, slots=True)
class ConflictRecordingResult:
    applied: bool
    conflict_ids: tuple[str, ...]
    transaction_id: str | None
    sequence: int
    previous_head: str
    new_head: str


class ConflictLedgerService:
    """Persist deterministic conflict candidates without changing Claim status."""

    def __init__(self, record_store: RecordStore):
        self.record_store = record_store

    def detect(self) -> tuple[ConflictCandidate, ...]:
        verification = self.record_store.verify()
        if not verification.valid:
            raise LedgerIntegrityError(
                "cannot detect conflicts from invalid ledger: "
                + ",".join(verification.reason_codes)
            )
        return detect_conflicts(replay_transactions(self.record_store.iterate()))

    def record_detected(
        self,
        *,
        actor: Actor,
        recorded_at: str,
        expected_head_hash: str,
        transaction_id: str | None = None,
    ) -> ConflictRecordingResult:
        expected = str(expected_head_hash or "").strip()
        if not expected:
            raise ConflictRecordingError("expected_head_hash is required")
        verification = self.record_store.verify()
        if not verification.valid:
            raise LedgerIntegrityError(
                "cannot record conflicts in invalid ledger: "
                + ",".join(verification.reason_codes)
            )
        current_head = verification.last_valid_hash or GENESIS_HASH
        if current_head != expected:
            raise HeadMismatchError(f"expected head {expected}, current head {current_head}")
        state = replay_transactions(self.record_store.iterate())
        if state.ledger_head != current_head:
            raise LedgerIntegrityError("replay state does not match verified ledger head")
        candidates = tuple(
            candidate
            for candidate in detect_conflicts(state)
            if candidate.conflict_id not in state.conflicts
        )
        if not candidates:
            return ConflictRecordingResult(
                applied=False,
                conflict_ids=(),
                transaction_id=None,
                sequence=verification.last_sequence or 0,
                previous_head=current_head,
                new_head=current_head,
            )
        transaction = LedgerTransaction.create(
            sequence=(verification.last_sequence or 0) + 1,
            prev_hash=current_head,
            actor=actor,
            recorded_at=recorded_at,
            transaction_id=transaction_id or f"txn_{uuid4().hex}",
            events=tuple(
                LedgerEvent(
                    type="conflict.detected",
                    payload=candidate.to_event_payload(detected_at=recorded_at),
                )
                for candidate in candidates
            ),
        )
        append = self.record_store.append(transaction, expected_head=expected)
        return ConflictRecordingResult(
            applied=True,
            conflict_ids=tuple(candidate.conflict_id for candidate in candidates),
            transaction_id=append.transaction_id,
            sequence=append.sequence,
            previous_head=append.previous_head,
            new_head=append.new_head,
        )


__all__ = [
    "ConflictLedgerService",
    "ConflictRecordingError",
    "ConflictRecordingResult",
]
