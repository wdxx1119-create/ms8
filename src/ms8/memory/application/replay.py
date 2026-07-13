"""Deterministic replay of authoritative ledger transactions."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from ..domain.ledger import GENESIS_HASH, LedgerTransaction, canonical_json
from ..domain.models import Claim, Decision, Evidence, MemoryEvent

_TARGET_STATUS_BY_ACTION = {
    "correct": "superseded",
    "supersede": "superseded",
    "revoke": "revoked",
    "forget": "revoked",
    "expire": "expired",
    "review_reject": "revoked",
}
_LOCATOR_TOKENS = (
    "offset",
    "line",
    "page",
    "path",
    "chunk",
    "span",
    "locator",
    "record_index",
    "section",
    "paragraph",
)


class ReplayIntegrityError(RuntimeError):
    """Raised when a valid ledger contains semantically inconsistent events."""


@dataclass(frozen=True, slots=True)
class ClaimReplayView:
    claim: Claim
    current_status: str
    decision_ids: tuple[str, ...]

    def to_logical_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim.claim_id,
            "current_status": self.current_status,
            "decision_ids": list(self.decision_ids),
            "claim": self.claim.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class ReplayState:
    ledger_head: str
    last_sequence: int
    memory_events: Mapping[str, MemoryEvent]
    claims: Mapping[str, ClaimReplayView]
    evidence: Mapping[str, Evidence]
    decisions: Mapping[str, Decision]
    conflicts: Mapping[str, Mapping[str, Any]]
    logical_state_hash: str

    def logical_manifest(self) -> dict[str, Any]:
        return {
            "ledger_head": self.ledger_head,
            "last_sequence": self.last_sequence,
            "memory_event_ids": sorted(self.memory_events),
            "claims": [self.claims[key].to_logical_dict() for key in sorted(self.claims)],
            "evidence_ids": sorted(self.evidence),
            "decision_ids": sorted(self.decisions),
            "conflicts": [dict(self.conflicts[key]) for key in sorted(self.conflicts)],
        }


def _logical_hash(payload: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _require_mapping(payload: object, event_type: str) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise ReplayIntegrityError(f"{event_type} payload must be an object")
    return payload


def _apply_decision(
    decision: Decision,
    claim_views: dict[str, ClaimReplayView],
) -> None:
    affected: dict[str, str] = {}
    target_status = _TARGET_STATUS_BY_ACTION.get(decision.action)
    if target_status is not None:
        for claim_id in decision.target_claim_ids:
            affected[claim_id] = target_status
    if decision.result_claim_id is not None and decision.result_status is not None:
        affected[decision.result_claim_id] = decision.result_status
    if not affected:
        return
    for claim_id, status in affected.items():
        current = claim_views.get(claim_id)
        if current is None:
            raise ReplayIntegrityError(f"decision {decision.decision_id} references unknown claim {claim_id}")
        claim_views[claim_id] = ClaimReplayView(
            claim=current.claim,
            current_status=status,
            decision_ids=(*current.decision_ids, decision.decision_id),
        )


def _effective_can_inject(
    view: ClaimReplayView,
    decisions: Mapping[str, Decision],
) -> bool:
    allowed = False
    for decision_id in view.decision_ids:
        decision = decisions.get(decision_id)
        if decision is None:
            continue
        governance = decision.policy.get("governance")
        if not isinstance(governance, Mapping):
            continue
        configured = governance.get("can_inject")
        if isinstance(configured, bool):
            allowed = configured
    return allowed


def _fragment_has_locator(fragment: Mapping[str, Any]) -> bool:
    return any(
        any(token in str(key).casefold() for token in _LOCATOR_TOKENS)
        and value not in (None, "", (), [], {})
        for key, value in fragment.items()
    )


def _validate_injectable_claims(
    *,
    memory_events: Mapping[str, MemoryEvent],
    claim_views: Mapping[str, ClaimReplayView],
    evidence_by_claim: Mapping[str, Sequence[Evidence]],
    decisions: Mapping[str, Decision],
    claim_ids: Iterable[str],
) -> None:
    """Validate only claims changed by the current immutable transaction.

    Validation still runs after every transaction, so a later transaction cannot
    retroactively repair an earlier injectable claim that lacked evidence. Limiting
    the scan to touched claims avoids quadratic replay cost on long ledgers.
    """

    for claim_id in dict.fromkeys(str(value) for value in claim_ids):
        view = claim_views.get(claim_id)
        if view is None or not _effective_can_inject(view, decisions):
            continue
        linked = evidence_by_claim.get(claim_id, ())
        if not linked:
            raise ReplayIntegrityError(
                f"injectable claim {claim_id} requires at least one evidence record"
            )
        traceable = False
        for evidence in linked:
            source_event = memory_events.get(evidence.event_id)
            if source_event is None or not source_event.source:
                continue
            if not evidence.quoted_text_hash.startswith("sha256:"):
                continue
            if not _fragment_has_locator(evidence.fragment):
                continue
            traceable = True
            break
        if not traceable:
            raise ReplayIntegrityError(
                f"injectable claim {claim_id} requires source, sha256 hash, and locator fragment"
            )


def replay_transactions(transactions: Iterable[LedgerTransaction]) -> ReplayState:
    """Replay a verified transaction sequence into deterministic logical state."""

    memory_events: dict[str, MemoryEvent] = {}
    claim_views: dict[str, ClaimReplayView] = {}
    evidence_items: dict[str, Evidence] = {}
    evidence_by_claim: dict[str, list[Evidence]] = {}
    decisions: dict[str, Decision] = {}
    conflicts: dict[str, Mapping[str, Any]] = {}
    expected_sequence = 1
    expected_prev_hash = GENESIS_HASH
    ledger_head = GENESIS_HASH
    last_sequence = 0

    for transaction in transactions:
        touched_claim_ids: set[str] = set()
        verification = transaction.verify(
            expected_prev_hash=expected_prev_hash,
            expected_sequence=expected_sequence,
        )
        if not verification.valid:
            raise ReplayIntegrityError(",".join(verification.reason_codes))
        for event in transaction.events:
            payload = _require_mapping(event.payload, event.type)
            if event.type == "memory_event.recorded":
                memory_event = MemoryEvent.from_dict(payload)
                if memory_event.event_id in memory_events:
                    raise ReplayIntegrityError(f"duplicate memory event: {memory_event.event_id}")
                memory_events[memory_event.event_id] = memory_event
            elif event.type == "claim.proposed":
                claim = Claim.from_dict(payload)
                if claim.claim_id in claim_views:
                    raise ReplayIntegrityError(f"duplicate claim: {claim.claim_id}")
                if claim.created_from_event_id not in memory_events:
                    raise ReplayIntegrityError(
                        f"claim {claim.claim_id} references unknown memory event {claim.created_from_event_id}"
                    )
                if claim.status != "proposed":
                    raise ReplayIntegrityError("claim.proposed must use proposed status")
                claim_views[claim.claim_id] = ClaimReplayView(claim, "proposed", ())
                touched_claim_ids.add(claim.claim_id)
            elif event.type == "evidence.linked":
                evidence = Evidence.from_dict(payload)
                if evidence.evidence_id in evidence_items:
                    raise ReplayIntegrityError(f"duplicate evidence: {evidence.evidence_id}")
                if evidence.claim_id not in claim_views:
                    raise ReplayIntegrityError(
                        f"evidence {evidence.evidence_id} references unknown claim {evidence.claim_id}"
                    )
                if evidence.event_id not in memory_events:
                    raise ReplayIntegrityError(
                        f"evidence {evidence.evidence_id} references unknown memory event {evidence.event_id}"
                    )
                evidence_items[evidence.evidence_id] = evidence
                evidence_by_claim.setdefault(evidence.claim_id, []).append(evidence)
                touched_claim_ids.add(evidence.claim_id)
            elif event.type == "decision.made":
                decision = Decision.from_dict(payload)
                if decision.decision_id in decisions:
                    raise ReplayIntegrityError(f"duplicate decision: {decision.decision_id}")
                for claim_id in decision.target_claim_ids:
                    if claim_id not in claim_views:
                        raise ReplayIntegrityError(
                            f"decision {decision.decision_id} references unknown claim {claim_id}"
                        )
                if decision.result_claim_id is not None and decision.result_claim_id not in claim_views:
                    raise ReplayIntegrityError(
                        f"decision {decision.decision_id} references unknown result claim "
                        f"{decision.result_claim_id}"
                    )
                decisions[decision.decision_id] = decision
                _apply_decision(decision, claim_views)
                touched_claim_ids.update(decision.target_claim_ids)
                if decision.result_claim_id is not None:
                    touched_claim_ids.add(decision.result_claim_id)
            elif event.type == "conflict.detected":
                conflict_id = str(payload.get("conflict_id") or "").strip()
                if not conflict_id:
                    raise ReplayIntegrityError("conflict.detected requires conflict_id")
                if conflict_id in conflicts:
                    raise ReplayIntegrityError(f"duplicate conflict: {conflict_id}")
                claim_ids = payload.get("claim_ids")
                if not isinstance(claim_ids, (list, tuple)) or len(claim_ids) < 2:
                    raise ReplayIntegrityError("conflict.detected requires at least two claim_ids")
                normalized_ids = [str(value) for value in claim_ids]
                missing = [claim_id for claim_id in normalized_ids if claim_id not in claim_views]
                if missing:
                    raise ReplayIntegrityError(
                        f"conflict {conflict_id} references unknown claims: {','.join(missing)}"
                    )
                conflicts[conflict_id] = MappingProxyType(
                    {**dict(payload), "claim_ids": tuple(normalized_ids)}
                )
            else:
                raise ReplayIntegrityError(f"unsupported ledger event type: {event.type}")

        _validate_injectable_claims(
            memory_events=memory_events,
            claim_views=claim_views,
            evidence_by_claim=evidence_by_claim,
            decisions=decisions,
            claim_ids=touched_claim_ids,
        )
        expected_prev_hash = transaction.hash
        ledger_head = transaction.hash
        last_sequence = transaction.sequence
        expected_sequence += 1

    manifest_without_hash = {
        "ledger_head": ledger_head,
        "last_sequence": last_sequence,
        "memory_event_ids": sorted(memory_events),
        "claims": [claim_views[key].to_logical_dict() for key in sorted(claim_views)],
        "evidence_ids": sorted(evidence_items),
        "decision_ids": sorted(decisions),
        "conflicts": [dict(conflicts[key]) for key in sorted(conflicts)],
    }
    return ReplayState(
        ledger_head=ledger_head,
        last_sequence=last_sequence,
        memory_events=MappingProxyType(memory_events),
        claims=MappingProxyType(claim_views),
        evidence=MappingProxyType(evidence_items),
        decisions=MappingProxyType(decisions),
        conflicts=MappingProxyType(conflicts),
        logical_state_hash=_logical_hash(manifest_without_hash),
    )
