"""Deterministic conflict detection and recommendation for replayed claims."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ..domain.ledger import canonical_json
from ..domain.models import Claim, _parse_datetime
from .replay import ClaimReplayView, ReplayState

_ACTIVE_STATUSES = {"proposed", "pending_review", "accepted", "verified", "disputed"}
_STATUS_RANK = {
    "verified": 5,
    "accepted": 4,
    "pending_review": 3,
    "proposed": 2,
    "disputed": 1,
}
_AUTHORITY_RANK = {
    "user_explicit": 6,
    "reviewer": 5,
    "system_verified": 4,
    "source_verified": 3,
    "user_implicit": 2,
    "inferred": 1,
}


@dataclass(frozen=True, slots=True)
class ConflictCandidate:
    conflict_id: str
    realm_id: str
    subject: str
    predicate: str
    claim_ids: tuple[str, ...]
    reason: str

    def to_event_payload(self, *, detected_at: str) -> dict[str, Any]:
        _parse_datetime(detected_at, "detected_at")
        return {
            "conflict_id": self.conflict_id,
            "realm_id": self.realm_id,
            "subject": self.subject,
            "predicate": self.predicate,
            "claim_ids": list(self.claim_ids),
            "reason": self.reason,
            "detected_at": detected_at,
        }


@dataclass(frozen=True, slots=True)
class ConflictAlternative:
    claim_id: str
    current_status: str
    authority: str
    confidence: float
    valid_time: dict[str, str | None]
    value: Any

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "current_status": self.current_status,
            "authority": self.authority,
            "confidence": self.confidence,
            "valid_time": dict(self.valid_time),
            "value": self.value,
        }


@dataclass(frozen=True, slots=True)
class ConflictRecommendation:
    conflict_id: str
    recommended_claim_id: str
    alternatives: tuple[ConflictAlternative, ...]
    explanation: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "conflict_id": self.conflict_id,
            "recommended_claim_id": self.recommended_claim_id,
            "candidates": [item.to_dict() for item in self.alternatives],
            "recommendation_explanation": list(self.explanation),
        }


def _bound(value: str | None, *, lower: bool) -> datetime:
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc) if lower else datetime.max.replace(tzinfo=timezone.utc)
    return _parse_datetime(value, "valid_time")


def valid_times_overlap(left: Claim, right: Claim) -> bool:
    """Use half-open intervals: ``start <= t < end`` when an end exists."""

    left_start = _bound(left.valid_time.start, lower=True)
    left_end = _bound(left.valid_time.end, lower=False)
    right_start = _bound(right.valid_time.start, lower=True)
    right_end = _bound(right.valid_time.end, lower=False)
    return left_start < right_end and right_start < left_end


def _different_values(left: Claim, right: Claim) -> bool:
    return canonical_json(left.to_dict()["value"]) != canonical_json(right.to_dict()["value"])


def _conflict_id(key: tuple[str, str, str], claim_ids: tuple[str, ...]) -> str:
    material = canonical_json({"key": list(key), "claim_ids": list(claim_ids)})
    return "conf_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def detect_conflicts(state: ReplayState) -> tuple[ConflictCandidate, ...]:
    """Detect overlapping, incompatible claims without deleting either side."""

    grouped: dict[tuple[str, str, str], list[ClaimReplayView]] = defaultdict(list)
    for view in state.claims.values():
        if view.current_status not in _ACTIVE_STATUSES:
            continue
        claim = view.claim
        grouped[(claim.realm_id, claim.subject, claim.predicate)].append(view)

    candidates: list[ConflictCandidate] = []
    for key in sorted(grouped):
        views = sorted(grouped[key], key=lambda item: item.claim.claim_id)
        involved: set[str] = set()
        for index, left in enumerate(views):
            for right in views[index + 1 :]:
                if _different_values(left.claim, right.claim) and valid_times_overlap(left.claim, right.claim):
                    involved.add(left.claim.claim_id)
                    involved.add(right.claim.claim_id)
        if len(involved) < 2:
            continue
        claim_ids = tuple(sorted(involved))
        candidates.append(
            ConflictCandidate(
                conflict_id=_conflict_id(key, claim_ids),
                realm_id=key[0],
                subject=key[1],
                predicate=key[2],
                claim_ids=claim_ids,
                reason="overlapping valid time with incompatible values",
            )
        )
    return tuple(candidates)


def _recommendation_key(view: ClaimReplayView) -> tuple[int, int, float, int, str]:
    claim = view.claim
    return (
        _STATUS_RANK.get(view.current_status, 0),
        _AUTHORITY_RANK.get(claim.authority, 0),
        claim.confidence,
        len(view.decision_ids),
        claim.claim_id,
    )


def recommend_conflict(state: ReplayState, conflict: ConflictCandidate) -> ConflictRecommendation:
    """Return all alternatives and a deterministic recommendation explanation."""

    views: list[ClaimReplayView] = []
    for claim_id in conflict.claim_ids:
        view = state.claims.get(claim_id)
        if view is None:
            raise ValueError(f"conflict references unknown claim: {claim_id}")
        views.append(view)
    ranked = sorted(views, key=_recommendation_key, reverse=True)
    winner = ranked[0]
    alternatives = tuple(
        ConflictAlternative(
            claim_id=view.claim.claim_id,
            current_status=view.current_status,
            authority=view.claim.authority,
            confidence=view.claim.confidence,
            valid_time=view.claim.valid_time.to_dict(),
            value=view.claim.to_dict()["value"],
        )
        for view in ranked
    )
    explanation = (
        f"status={winner.current_status} ranked above lower lifecycle states",
        f"authority={winner.claim.authority} used as the next tie-breaker",
        f"confidence={winner.claim.confidence:.6f} used after status and authority",
        "all alternatives remain retained and auditable",
    )
    return ConflictRecommendation(
        conflict_id=conflict.conflict_id,
        recommended_claim_id=winner.claim.claim_id,
        alternatives=alternatives,
        explanation=explanation,
    )


def describe_recorded_conflict(state: ReplayState, conflict_id: str) -> dict[str, Any]:
    """Return a recorded conflict with all candidates and recommendation reasons."""

    normalized = str(conflict_id or "").strip()
    payload = state.conflicts.get(normalized)
    if payload is None:
        raise ValueError(f"unknown recorded conflict: {normalized}")
    raw_claim_ids = payload.get("claim_ids", ())
    if not isinstance(raw_claim_ids, (list, tuple)) or len(raw_claim_ids) < 2:
        raise ValueError(f"recorded conflict {normalized} has invalid claim_ids")
    claim_ids = tuple(str(value) for value in raw_claim_ids)
    first = state.claims.get(claim_ids[0])
    if first is None:
        raise ValueError(f"recorded conflict {normalized} references unknown claims")
    candidate = ConflictCandidate(
        conflict_id=normalized,
        realm_id=str(payload.get("realm_id") or first.claim.realm_id),
        subject=str(payload.get("subject") or first.claim.subject),
        predicate=str(payload.get("predicate") or first.claim.predicate),
        claim_ids=claim_ids,
        reason=str(payload.get("reason") or "recorded claim conflict"),
    )
    recommendation = recommend_conflict(state, candidate)
    return {
        "conflict_id": normalized,
        "realm_id": candidate.realm_id,
        "subject": candidate.subject,
        "predicate": candidate.predicate,
        "reason": candidate.reason,
        **recommendation.to_dict(),
    }


__all__ = [
    "ConflictAlternative",
    "ConflictCandidate",
    "ConflictRecommendation",
    "describe_recorded_conflict",
    "detect_conflicts",
    "recommend_conflict",
    "valid_times_overlap",
]
