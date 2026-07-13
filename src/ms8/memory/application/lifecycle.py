"""Guarded lifecycle mutations for ledger-v1 claims.

All mutations append immutable ledger transactions. The service never edits a
claim or projection in place and requires an optimistic expected-head token for
every operation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from ..domain.ledger import GENESIS_HASH, LedgerEvent, LedgerTransaction
from ..domain.models import Actor, Claim, Decision
from ..ports.record_store import HeadMismatchError, LedgerIntegrityError, RecordStore
from .lifecycle_policy import (
    LifecycleAuthorizationError,
    LifecyclePolicyGrant,
    LifecyclePolicyVerifierFn,
    require_lifecycle_authorization,
)
from .replay import ClaimReplayView, ReplayState, replay_transactions

_TERMINAL_STATUSES = {"superseded", "revoked", "expired"}


class LifecycleMutationError(ValueError):
    """Raised when a requested lifecycle transition violates domain rules."""


@dataclass(frozen=True, slots=True)
class LifecycleMutationResult:
    transaction_id: str
    sequence: int
    previous_head: str
    new_head: str
    decision_ids: tuple[str, ...]
    result_claim_id: str | None


class MemoryLifecycleService:
    """Append lifecycle decisions under optimistic concurrency control."""

    def __init__(
        self,
        record_store: RecordStore,
        *,
        policy_verifier: LifecyclePolicyVerifierFn | None = None,
    ):
        self.record_store = record_store
        self.policy_verifier = policy_verifier

    def _state_at_head(self, expected_head_hash: str) -> tuple[ReplayState, int]:
        expected = str(expected_head_hash or "").strip()
        if not expected:
            raise LifecycleMutationError("expected_head_hash is required")
        verification = self.record_store.verify()
        if not verification.valid:
            raise LedgerIntegrityError(
                "cannot mutate invalid ledger: " + ",".join(verification.reason_codes)
            )
        current_head = verification.last_valid_hash or GENESIS_HASH
        if current_head != expected:
            raise HeadMismatchError(f"expected head {expected}, current head {current_head}")
        state = replay_transactions(self.record_store.iterate())
        if state.ledger_head != current_head:
            raise LedgerIntegrityError("replay state does not match verified ledger head")
        return state, verification.last_sequence or 0

    @staticmethod
    def _claim_view(state: ReplayState, claim_id: str) -> ClaimReplayView:
        normalized = str(claim_id or "").strip()
        if not normalized:
            raise LifecycleMutationError("claim_id is required")
        view = state.claims.get(normalized)
        if view is None:
            raise LifecycleMutationError(f"unknown claim: {normalized}")
        return view

    @staticmethod
    def _require_mutable(view: ClaimReplayView) -> None:
        if view.current_status in _TERMINAL_STATUSES:
            raise LifecycleMutationError(
                f"claim {view.claim.claim_id} is already terminal: {view.current_status}"
            )

    @staticmethod
    def _policy_payload(policy: Mapping[str, Any] | None) -> dict[str, Any]:
        return dict(policy or {})

    @staticmethod
    def _require_new_decision_ids(state: ReplayState, decision_ids: Sequence[str]) -> None:
        normalized = tuple(str(value or "").strip() for value in decision_ids)
        if any(not value for value in normalized):
            raise LifecycleMutationError("decision_id must not be empty")
        if len(set(normalized)) != len(normalized):
            raise LifecycleMutationError("decision_ids must be unique")
        duplicate = next((value for value in normalized if value in state.decisions), None)
        if duplicate is not None:
            raise LifecycleMutationError(f"decision already exists: {duplicate}")

    def _authorize_decision(
        self,
        *,
        decision: Decision,
        actor: Actor,
        recorded_at: str,
    ) -> LifecyclePolicyGrant | None:
        raw = decision.policy.get("policy_authorization")
        grant = LifecyclePolicyGrant.from_dict(raw) if isinstance(raw, Mapping) else None
        try:
            return require_lifecycle_authorization(
                action=decision.action,
                target_claim_ids=decision.target_claim_ids,
                actor=actor,
                recorded_at=recorded_at,
                grant=grant,
                verifier=self.policy_verifier,
            )
        except LifecycleAuthorizationError as exc:
            raise LifecycleMutationError(str(exc)) from exc

    def _append(
        self,
        *,
        state: ReplayState,
        last_sequence: int,
        expected_head_hash: str,
        actor: Actor,
        recorded_at: str,
        events: Sequence[LedgerEvent],
        decision_ids: Sequence[str],
        result_claim_id: str | None,
        transaction_id: str | None,
    ) -> LifecycleMutationResult:
        self._require_new_decision_ids(state, decision_ids)
        for event in events:
            if event.type != "decision.made":
                continue
            decision = Decision.from_dict(event.payload)
            self._authorize_decision(
                decision=decision,
                actor=actor,
                recorded_at=recorded_at,
            )
        transaction = LedgerTransaction.create(
            sequence=last_sequence + 1,
            actor=actor,
            recorded_at=recorded_at,
            prev_hash=state.ledger_head,
            events=events,
            transaction_id=transaction_id or f"txn_{uuid4().hex}",
        )
        current_transactions = tuple(self.record_store.iterate())
        replay_transactions((*current_transactions, transaction))
        append = self.record_store.append(transaction, expected_head=expected_head_hash)
        return LifecycleMutationResult(
            transaction_id=append.transaction_id,
            sequence=append.sequence,
            previous_head=append.previous_head,
            new_head=append.new_head,
            decision_ids=tuple(decision_ids),
            result_claim_id=result_claim_id,
        )

    def _replace(
        self,
        *,
        action: str,
        target_claim_id: str,
        replacement: Claim,
        actor: Actor,
        reason: str,
        recorded_at: str,
        expected_head_hash: str,
        result_status: str = "accepted",
        policy: Mapping[str, Any] | None = None,
        decision_id: str | None = None,
        transaction_id: str | None = None,
    ) -> LifecycleMutationResult:
        state, last_sequence = self._state_at_head(expected_head_hash)
        target = self._claim_view(state, target_claim_id)
        self._require_mutable(target)
        if replacement.status != "proposed":
            raise LifecycleMutationError("replacement claim must use proposed status")
        if replacement.claim_id in state.claims:
            raise LifecycleMutationError(f"replacement claim already exists: {replacement.claim_id}")
        if replacement.created_from_event_id not in state.memory_events:
            raise LifecycleMutationError(
                "replacement claim must reference an existing memory event"
            )
        if (
            replacement.realm_id,
            replacement.subject,
            replacement.predicate,
        ) != (
            target.claim.realm_id,
            target.claim.subject,
            target.claim.predicate,
        ):
            raise LifecycleMutationError(
                "replacement must preserve realm_id, subject, and predicate"
            )
        resolved_decision_id = decision_id or f"dec_{uuid4().hex}"
        decision = Decision(
            decision_id=resolved_decision_id,
            action=action,
            target_claim_ids=(target.claim.claim_id,),
            result_claim_id=replacement.claim_id,
            result_status=result_status,
            policy=self._policy_payload(policy),
            actor=actor,
            reason=reason,
            recorded_at=recorded_at,
        )
        return self._append(
            state=state,
            last_sequence=last_sequence,
            expected_head_hash=expected_head_hash,
            actor=actor,
            recorded_at=recorded_at,
            events=(
                LedgerEvent(type="claim.proposed", payload=replacement.to_dict()),
                LedgerEvent(type="decision.made", payload=decision.to_dict()),
            ),
            decision_ids=(resolved_decision_id,),
            result_claim_id=replacement.claim_id,
            transaction_id=transaction_id,
        )

    def correct(
        self,
        *,
        target_claim_id: str,
        replacement: Claim,
        actor: Actor,
        reason: str,
        recorded_at: str,
        expected_head_hash: str,
        result_status: str = "accepted",
        policy: Mapping[str, Any] | None = None,
        decision_id: str | None = None,
        transaction_id: str | None = None,
    ) -> LifecycleMutationResult:
        return self._replace(
            action="correct",
            target_claim_id=target_claim_id,
            replacement=replacement,
            actor=actor,
            reason=reason,
            recorded_at=recorded_at,
            expected_head_hash=expected_head_hash,
            result_status=result_status,
            policy=policy,
            decision_id=decision_id,
            transaction_id=transaction_id,
        )

    def supersede(
        self,
        *,
        target_claim_id: str,
        replacement: Claim,
        actor: Actor,
        reason: str,
        recorded_at: str,
        expected_head_hash: str,
        result_status: str = "accepted",
        policy: Mapping[str, Any] | None = None,
        decision_id: str | None = None,
        transaction_id: str | None = None,
    ) -> LifecycleMutationResult:
        return self._replace(
            action="supersede",
            target_claim_id=target_claim_id,
            replacement=replacement,
            actor=actor,
            reason=reason,
            recorded_at=recorded_at,
            expected_head_hash=expected_head_hash,
            result_status=result_status,
            policy=policy,
            decision_id=decision_id,
            transaction_id=transaction_id,
        )

    def _terminal_decision(
        self,
        *,
        action: str,
        target_claim_id: str,
        actor: Actor,
        reason: str,
        recorded_at: str,
        expected_head_hash: str,
        policy: Mapping[str, Any] | None = None,
        decision_id: str | None = None,
        transaction_id: str | None = None,
    ) -> LifecycleMutationResult:
        state, last_sequence = self._state_at_head(expected_head_hash)
        target = self._claim_view(state, target_claim_id)
        self._require_mutable(target)
        resolved_decision_id = decision_id or f"dec_{uuid4().hex}"
        resolved_policy = self._policy_payload(policy)
        if action == "forget":
            resolved_policy = {
                **resolved_policy,
                "logical_tombstone": {
                    "claim_id": target.claim.claim_id,
                    "realm_id": target.claim.realm_id,
                    "action": "forget",
                },
            }
        decision = Decision(
            decision_id=resolved_decision_id,
            action=action,
            target_claim_ids=(target.claim.claim_id,),
            policy=resolved_policy,
            actor=actor,
            reason=reason,
            recorded_at=recorded_at,
        )
        return self._append(
            state=state,
            last_sequence=last_sequence,
            expected_head_hash=expected_head_hash,
            actor=actor,
            recorded_at=recorded_at,
            events=(LedgerEvent(type="decision.made", payload=decision.to_dict()),),
            decision_ids=(resolved_decision_id,),
            result_claim_id=None,
            transaction_id=transaction_id,
        )

    def revoke(
        self,
        *,
        target_claim_id: str,
        actor: Actor,
        reason: str,
        recorded_at: str,
        expected_head_hash: str,
        policy: Mapping[str, Any] | None = None,
        decision_id: str | None = None,
        transaction_id: str | None = None,
    ) -> LifecycleMutationResult:
        return self._terminal_decision(
            action="revoke",
            target_claim_id=target_claim_id,
            actor=actor,
            reason=reason,
            recorded_at=recorded_at,
            expected_head_hash=expected_head_hash,
            policy=policy,
            decision_id=decision_id,
            transaction_id=transaction_id,
        )

    def forget(
        self,
        *,
        target_claim_id: str,
        actor: Actor,
        reason: str,
        recorded_at: str,
        expected_head_hash: str,
        policy: Mapping[str, Any] | None = None,
        decision_id: str | None = None,
        transaction_id: str | None = None,
    ) -> LifecycleMutationResult:
        return self._terminal_decision(
            action="forget",
            target_claim_id=target_claim_id,
            actor=actor,
            reason=reason,
            recorded_at=recorded_at,
            expected_head_hash=expected_head_hash,
            policy=policy,
            decision_id=decision_id,
            transaction_id=transaction_id,
        )

    def expire(
        self,
        *,
        target_claim_id: str,
        actor: Actor,
        reason: str,
        recorded_at: str,
        expected_head_hash: str,
        policy: Mapping[str, Any] | None = None,
        decision_id: str | None = None,
        transaction_id: str | None = None,
    ) -> LifecycleMutationResult:
        return self._terminal_decision(
            action="expire",
            target_claim_id=target_claim_id,
            actor=actor,
            reason=reason,
            recorded_at=recorded_at,
            expected_head_hash=expected_head_hash,
            policy=policy,
            decision_id=decision_id,
            transaction_id=transaction_id,
        )

    def resolve_conflict(
        self,
        *,
        conflict_id: str,
        winning_claim_id: str,
        claim_ids: Sequence[str],
        actor: Actor,
        reason: str,
        recorded_at: str,
        expected_head_hash: str,
        policy: Mapping[str, Any] | None = None,
        decision_id: str | None = None,
        transaction_id: str | None = None,
    ) -> LifecycleMutationResult:
        state, last_sequence = self._state_at_head(expected_head_hash)
        normalized_conflict_id = str(conflict_id or "").strip()
        if not normalized_conflict_id:
            raise LifecycleMutationError("conflict_id is required")
        normalized_ids = tuple(
            dict.fromkeys(
                str(value).strip() for value in claim_ids if str(value).strip()
            )
        )
        if len(normalized_ids) < 2:
            raise LifecycleMutationError("conflict resolution requires at least two claims")
        winner = str(winning_claim_id or "").strip()
        if winner not in normalized_ids:
            raise LifecycleMutationError("winning_claim_id must be included in claim_ids")
        for claim_id in normalized_ids:
            self._require_mutable(self._claim_view(state, claim_id))
        recorded_conflict = state.conflicts.get(normalized_conflict_id)
        if recorded_conflict is not None:
            recorded_ids = {str(value) for value in recorded_conflict.get("claim_ids", ())}
            if set(normalized_ids) != recorded_ids:
                raise LifecycleMutationError("claim_ids do not match the recorded conflict")

        resolved_decision_id = decision_id or f"dec_{uuid4().hex}"
        base_policy = {
            **self._policy_payload(policy),
            "conflict_id": normalized_conflict_id,
            "winning_claim_id": winner,
            "retained_alternatives": [value for value in normalized_ids if value != winner],
        }
        resolution = Decision(
            decision_id=resolved_decision_id,
            action="resolve_conflict",
            target_claim_ids=normalized_ids,
            result_claim_id=winner,
            result_status="accepted",
            policy=base_policy,
            actor=actor,
            reason=reason,
            recorded_at=recorded_at,
        )
        events: list[LedgerEvent] = [
            LedgerEvent(type="decision.made", payload=resolution.to_dict())
        ]
        decision_ids = [resolved_decision_id]
        for losing_claim_id in normalized_ids:
            if losing_claim_id == winner:
                continue
            loser_decision_id = f"dec_{uuid4().hex}"
            loser_policy = {
                "conflict_id": normalized_conflict_id,
                "resolved_in_favor_of": winner,
                "retained_as_alternative": True,
            }
            authorization_payload = base_policy.get("policy_authorization")
            if isinstance(authorization_payload, Mapping):
                loser_policy["policy_authorization"] = dict(authorization_payload)
            loser = Decision(
                decision_id=loser_decision_id,
                action="revoke",
                target_claim_ids=(losing_claim_id,),
                policy=loser_policy,
                actor=actor,
                reason=f"Conflict resolved in favor of {winner}: {reason}",
                recorded_at=recorded_at,
            )
            events.append(LedgerEvent(type="decision.made", payload=loser.to_dict()))
            decision_ids.append(loser_decision_id)
        return self._append(
            state=state,
            last_sequence=last_sequence,
            expected_head_hash=expected_head_hash,
            actor=actor,
            recorded_at=recorded_at,
            events=events,
            decision_ids=decision_ids,
            result_claim_id=winner,
            transaction_id=transaction_id,
        )


__all__ = [
    "LifecycleMutationError",
    "LifecycleMutationResult",
    "MemoryLifecycleService",
]
