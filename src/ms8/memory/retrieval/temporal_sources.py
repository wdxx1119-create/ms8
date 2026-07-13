"""Deterministic temporal candidate retrieval over an authorized replay state."""

from __future__ import annotations

from collections.abc import Sequence

from ..application.replay import ReplayState
from ..application.temporal_query import effective_valid_until
from .adapters import CandidateRecord, ProjectionCandidateSource
from .analyzer import analyze_query
from .candidate_sources import CandidateSourceError
from .models import RetrievalPlan

_HISTORICAL_STATUSES = frozenset({"superseded", "expired"})
_CURRENT_STATUSES = frozenset({"accepted", "verified", "disputed"})
_UNKNOWN_TIME_BASES = frozenset({"", "unknown", "unspecified", "inferred_unknown"})


def _claim_terms(text: str) -> frozenset[str]:
    try:
        return frozenset(analyze_query(text).tokens)
    except ValueError:
        return frozenset()


def _evidence_ids(state: ReplayState, claim_id: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            evidence_id
            for evidence_id, evidence in state.evidence.items()
            if evidence.claim_id == claim_id
        )
    )


def _historical_requested(plan: RetrievalPlan) -> bool:
    return plan.query.purpose == "historical" or plan.intent == "historical_reason"


class TemporalReplayCandidateProvider:
    """Retrieve current or historical claims without widening eligibility."""

    def __init__(self, state: ReplayState) -> None:
        if not isinstance(state, ReplayState):
            raise TypeError("state must be ReplayState")
        self.state = state

    def __call__(
        self,
        plan: RetrievalPlan,
        eligible_claim_ids: tuple[str, ...],
        limit: int,
    ) -> Sequence[CandidateRecord]:
        if not isinstance(plan, RetrievalPlan):
            raise TypeError("plan must be RetrievalPlan")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")

        historical = _historical_requested(plan)
        query_terms = _claim_terms(plan.query.text)
        records: list[CandidateRecord] = []
        for claim_id in eligible_claim_ids:
            view = self.state.claims.get(claim_id)
            if view is None:
                raise CandidateSourceError(
                    f"temporal eligibility references a missing claim: {claim_id}"
                )
            status = view.current_status
            effective_end = effective_valid_until(self.state, view)
            is_historical = status in _HISTORICAL_STATUSES
            if historical:
                if not is_historical:
                    continue
                mode = "historical"
            else:
                if is_historical or status not in _CURRENT_STATUSES:
                    continue
                mode = "current"

            claim = view.claim
            claim_terms = _claim_terms(
                " ".join((claim.text, claim.subject, claim.predicate, str(claim.value)))
            )
            matched_terms = tuple(sorted(query_terms.intersection(claim_terms)))
            if query_terms and not matched_terms:
                continue

            evidence_ids = _evidence_ids(self.state, claim_id)
            if not evidence_ids:
                continue
            relevance = len(matched_terms) / max(1, len(query_terms))
            basis = str(claim.valid_time.basis or "").strip().casefold()
            supplementary = basis in _UNKNOWN_TIME_BASES
            mode_weight = 0.9 if mode == "historical" else 1.0
            basis_weight = 0.55 if supplementary else 1.0
            score = round((0.35 + 0.65 * relevance) * mode_weight * basis_weight, 12)
            records.append(
                CandidateRecord(
                    claim_id=claim_id,
                    evidence_ids=evidence_ids,
                    score=score,
                    reason={
                        "temporal_mode": mode,
                        "current_status": status,
                        "valid_from": claim.valid_time.start,
                        "valid_until": effective_end,
                        "time_basis": basis or "unknown",
                        "supplementary": supplementary,
                        "matched_terms": matched_terms,
                    },
                )
            )

        records.sort(key=lambda item: (-item.score, item.claim_id, item.evidence_ids))
        return tuple(records[:limit])


class TemporalReplayCandidateSource(ProjectionCandidateSource):
    """Temporal-channel adapter over one verified replay state."""

    def __init__(self, provider: TemporalReplayCandidateProvider) -> None:
        super().__init__(name="temporal-replay", channel="temporal", provider=provider)


__all__ = [
    "TemporalReplayCandidateProvider",
    "TemporalReplayCandidateSource",
]
