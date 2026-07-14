"""Candidate-source contract for governed Hybrid Retrieval v1."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .eligibility import EligibleClaims
from .models import CandidateChannel, CandidateHit, RetrievalPlan


class CandidateSourceError(RuntimeError):
    """Raised when a candidate source violates the retrieval contract."""


@runtime_checkable
class CandidateSource(Protocol):
    """A retriever that can only operate inside an immutable eligibility set."""

    name: str
    channel: CandidateChannel

    def retrieve(self, plan: RetrievalPlan, eligible: EligibleClaims) -> tuple[CandidateHit, ...]: ...


def validate_candidate_hits(
    *,
    source: CandidateSource,
    plan: RetrievalPlan,
    eligible: EligibleClaims,
    hits: tuple[CandidateHit, ...],
) -> tuple[CandidateHit, ...]:
    """Fail closed when a source widens eligibility or returns malformed ranks."""

    if not isinstance(plan, RetrievalPlan):
        raise TypeError("plan must be RetrievalPlan")
    if not isinstance(eligible, EligibleClaims):
        raise TypeError("eligible must be EligibleClaims")
    if not isinstance(hits, tuple):
        raise TypeError("candidate source must return a tuple")

    source_name = str(source.name or "").strip()
    if not source_name:
        raise CandidateSourceError("candidate source name must not be empty")
    if source.channel not in {"lexical", "vector", "entity", "temporal", "graph"}:
        raise CandidateSourceError(f"unsupported candidate source channel: {source.channel}")

    limit = plan.candidate_limits.for_channel(source.channel)
    seen_claims: set[str] = set()
    previous_rank = 0
    validated: list[CandidateHit] = []
    for hit in hits:
        if not isinstance(hit, CandidateHit):
            raise TypeError("candidate source returned a non-CandidateHit value")
        if hit.channel != source.channel:
            raise CandidateSourceError(
                f"candidate channel mismatch: source={source.channel} hit={hit.channel}"
            )
        if not eligible.allows(hit.claim_id):
            raise CandidateSourceError(
                f"candidate source attempted to widen eligibility: source={source_name} claim={hit.claim_id}"
            )
        if hit.claim_id in seen_claims:
            raise CandidateSourceError(
                f"candidate source returned a duplicate claim: source={source_name} claim={hit.claim_id}"
            )
        if hit.rank <= previous_rank:
            raise CandidateSourceError(
                f"candidate ranks must be strictly increasing: source={source_name} rank={hit.rank}"
            )
        if hit.rank > limit:
            raise CandidateSourceError(
                f"candidate rank exceeds plan limit: source={source_name} rank={hit.rank} limit={limit}"
            )
        eligible.require(hit.claim_id)
        seen_claims.add(hit.claim_id)
        previous_rank = hit.rank
        validated.append(hit)
    return tuple(validated)


def run_candidate_source(
    source: CandidateSource,
    plan: RetrievalPlan,
    eligible: EligibleClaims,
) -> tuple[CandidateHit, ...]:
    """Execute a source and enforce the common output boundary."""

    if not isinstance(eligible, EligibleClaims):
        raise TypeError("eligible must be EligibleClaims")
    hits = source.retrieve(plan, eligible)
    return validate_candidate_hits(source=source, plan=plan, eligible=eligible, hits=hits)


__all__ = [
    "CandidateSource",
    "CandidateSourceError",
    "run_candidate_source",
    "validate_candidate_hits",
]
