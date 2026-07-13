"""Three-time claim queries over immutable ledger transactions."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..domain.ledger import LedgerTransaction
from ..domain.models import Claim, _parse_datetime
from .replay import ClaimReplayView, ReplayState, replay_transactions

_DEFAULT_VISIBLE_STATUSES = {"proposed", "pending_review", "accepted", "verified", "disputed"}
_REPLACEMENT_ACTIONS = {"correct", "supersede"}


class TemporalQueryError(ValueError):
    """Raised when temporal query input or ledger time ordering is invalid."""


@dataclass(frozen=True, slots=True)
class ClaimQueryResult:
    claim_id: str
    current_status: str
    decision_ids: tuple[str, ...]
    claim: Claim | None
    tombstone: dict[str, Any] | None
    effective_valid_from: str | None
    effective_valid_until: str | None

    @property
    def forgotten(self) -> bool:
        return self.tombstone is not None


def _utc(value: str, field_name: str) -> datetime:
    return _parse_datetime(value, field_name)


def _recorded_prefix(
    transactions: Iterable[LedgerTransaction],
    recorded_as_of: str | None,
) -> tuple[LedgerTransaction, ...]:
    ordered = tuple(transactions)
    previous_time: datetime | None = None
    cutoff = _utc(recorded_as_of, "recorded_as_of") if recorded_as_of is not None else None
    prefix: list[LedgerTransaction] = []
    stopped = False
    for transaction in ordered:
        current_time = _utc(transaction.recorded_at, "transaction.recorded_at")
        if previous_time is not None and current_time < previous_time:
            raise TemporalQueryError("ledger transaction recorded_at values must be monotonic")
        previous_time = current_time
        if cutoff is not None and current_time > cutoff:
            stopped = True
            continue
        if stopped:
            raise TemporalQueryError(
                "recorded_as_of would select a non-prefix transaction sequence"
            )
        prefix.append(transaction)
    return tuple(prefix)


def replay_recorded_as_of(
    transactions: Iterable[LedgerTransaction],
    recorded_as_of: str | None,
) -> ReplayState:
    """Replay the authoritative transaction prefix known by ``recorded_as_of``."""

    return replay_transactions(_recorded_prefix(transactions, recorded_as_of))


def _earlier(left: str | None, right: str | None) -> str | None:
    if left is None:
        return right
    if right is None:
        return left
    return left if _utc(left, "valid_time.end") <= _utc(right, "valid_time.end") else right


def effective_valid_until(state: ReplayState, view: ClaimReplayView) -> str | None:
    """Derive immutable valid-time closure from correction/supersede Decisions."""

    derived_end = view.claim.valid_time.end
    for decision_id in view.decision_ids:
        decision = state.decisions.get(decision_id)
        if decision is None or decision.action not in _REPLACEMENT_ACTIONS:
            continue
        if view.claim.claim_id not in decision.target_claim_ids:
            continue
        candidate = decision.recorded_at
        if decision.result_claim_id is not None:
            replacement = state.claims.get(decision.result_claim_id)
            if replacement is not None and replacement.claim.valid_time.start is not None:
                candidate = replacement.claim.valid_time.start
        derived_end = _earlier(derived_end, candidate)
    return derived_end


def claim_is_valid_at(
    claim: Claim,
    valid_at: str | None,
    *,
    effective_end: str | None = None,
) -> bool:
    if valid_at is None:
        return True
    instant = _utc(valid_at, "valid_at")
    if claim.valid_time.start is not None:
        start = _utc(claim.valid_time.start, "valid_time.start")
        if instant < start:
            return False
    end_value = _earlier(claim.valid_time.end, effective_end)
    if end_value is not None:
        end = _utc(end_value, "valid_time.end")
        if instant >= end:
            return False
    return True


def claim_was_observed_as_of(
    state: ReplayState,
    view: ClaimReplayView,
    observed_as_of: str | None,
) -> bool:
    """Filter by source observation time independently of recorded and valid time."""

    if observed_as_of is None:
        return True
    source_event = state.memory_events.get(view.claim.created_from_event_id)
    if source_event is None:
        raise TemporalQueryError(
            f"claim {view.claim.claim_id} references a missing source event"
        )
    return _utc(source_event.observed_at, "memory_event.observed_at") <= _utc(
        observed_as_of,
        "observed_as_of",
    )


def _latest_action(state: ReplayState, view: ClaimReplayView) -> str | None:
    if not view.decision_ids:
        return None
    decision = state.decisions.get(view.decision_ids[-1])
    return decision.action if decision is not None else None


def _forgotten_tombstone(state: ReplayState, view: ClaimReplayView) -> dict[str, Any] | None:
    if _latest_action(state, view) != "forget":
        return None
    decision_id = view.decision_ids[-1]
    decision = state.decisions[decision_id]
    configured = decision.policy.get("logical_tombstone")
    tombstone = dict(configured) if isinstance(configured, Mapping) else {}
    return {
        "claim_id": view.claim.claim_id,
        "realm_id": view.claim.realm_id,
        "current_status": view.current_status,
        "decision_id": decision_id,
        **tombstone,
    }


def query_claims(
    transactions: Iterable[LedgerTransaction],
    *,
    recorded_as_of: str | None = None,
    observed_as_of: str | None = None,
    valid_at: str | None = None,
    include_inactive: bool = False,
    include_forgotten_tombstones: bool = False,
) -> tuple[ClaimQueryResult, ...]:
    """Query claims with separate recorded, observed, and fact-valid coordinates."""

    state = replay_recorded_as_of(transactions, recorded_as_of)
    results: list[ClaimQueryResult] = []
    for claim_id in sorted(state.claims):
        view = state.claims[claim_id]
        tombstone = _forgotten_tombstone(state, view)
        if tombstone is not None:
            if include_forgotten_tombstones:
                results.append(
                    ClaimQueryResult(
                        claim_id=claim_id,
                        current_status=view.current_status,
                        decision_ids=view.decision_ids,
                        claim=None,
                        tombstone=tombstone,
                        effective_valid_from=None,
                        effective_valid_until=None,
                    )
                )
            continue
        if not include_inactive and view.current_status not in _DEFAULT_VISIBLE_STATUSES:
            continue
        if not claim_was_observed_as_of(state, view, observed_as_of):
            continue
        effective_end = effective_valid_until(state, view)
        if not claim_is_valid_at(view.claim, valid_at, effective_end=effective_end):
            continue
        results.append(
            ClaimQueryResult(
                claim_id=claim_id,
                current_status=view.current_status,
                decision_ids=view.decision_ids,
                claim=view.claim,
                tombstone=None,
                effective_valid_from=view.claim.valid_time.start,
                effective_valid_until=effective_end,
            )
        )
    return tuple(results)


def query_as_of(
    transactions: Iterable[LedgerTransaction],
    *,
    as_of: str,
    include_inactive: bool = False,
    include_forgotten_tombstones: bool = False,
) -> tuple[ClaimQueryResult, ...]:
    """Convenience query applying one instant to all three time coordinates."""

    return query_claims(
        transactions,
        recorded_as_of=as_of,
        observed_as_of=as_of,
        valid_at=as_of,
        include_inactive=include_inactive,
        include_forgotten_tombstones=include_forgotten_tombstones,
    )


__all__ = [
    "ClaimQueryResult",
    "TemporalQueryError",
    "claim_is_valid_at",
    "claim_was_observed_as_of",
    "effective_valid_until",
    "query_as_of",
    "query_claims",
    "replay_recorded_as_of",
]
