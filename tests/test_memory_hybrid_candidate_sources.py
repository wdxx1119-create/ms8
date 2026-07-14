from __future__ import annotations

import pytest

from ms8.memory.retrieval import (
    CandidateHit,
    CandidateSourceError,
    EligibleClaims,
    MemoryQuery,
    Principal,
    RetrievalPlan,
    run_candidate_source,
)


class _Source:
    name = "fixture"
    channel = "lexical"

    def __init__(self, hits: tuple[CandidateHit, ...]) -> None:
        self.hits = hits

    def retrieve(self, plan: RetrievalPlan, eligible: EligibleClaims) -> tuple[CandidateHit, ...]:
        assert isinstance(plan, RetrievalPlan)
        assert isinstance(eligible, EligibleClaims)
        return self.hits


def _plan() -> RetrievalPlan:
    principal = Principal(
        principal_id="user:test",
        kind="user",
        realm_ids=("realm:project",),
        capabilities=("recall",),
    )
    return RetrievalPlan(
        query=MemoryQuery(text="sqlite", purpose="recall", realm_ids=("realm:project",)),
        principal=principal,
        intent="open_recall",
        realm_ids=("realm:project",),
    )


def _hit(claim_id: str, rank: int) -> CandidateHit:
    return CandidateHit(
        claim_id=claim_id,
        evidence_ids=(f"evd:{claim_id}",),
        channel="lexical",
        rank=rank,
        raw_score=1.0 / rank,
        reason={"fixture": True},
    )


def test_candidate_source_runs_inside_eligibility_set() -> None:
    eligible = EligibleClaims(claim_ids=("clm_1", "clm_2"), evaluated_count=2)
    source = _Source((_hit("clm_1", 1), _hit("clm_2", 2)))

    assert run_candidate_source(source, _plan(), eligible) == source.hits


def test_candidate_source_cannot_widen_eligibility() -> None:
    eligible = EligibleClaims(claim_ids=("clm_1",), evaluated_count=2)
    source = _Source((_hit("clm_2", 1),))

    with pytest.raises(CandidateSourceError, match="attempted to widen eligibility"):
        run_candidate_source(source, _plan(), eligible)


def test_candidate_source_rejects_duplicate_claims() -> None:
    eligible = EligibleClaims(claim_ids=("clm_1",), evaluated_count=1)
    source = _Source((_hit("clm_1", 1), _hit("clm_1", 2)))

    with pytest.raises(CandidateSourceError, match="duplicate claim"):
        run_candidate_source(source, _plan(), eligible)


def test_candidate_source_rejects_non_increasing_ranks() -> None:
    eligible = EligibleClaims(claim_ids=("clm_1", "clm_2"), evaluated_count=2)
    source = _Source((_hit("clm_1", 2), _hit("clm_2", 1)))

    with pytest.raises(CandidateSourceError, match="strictly increasing"):
        run_candidate_source(source, _plan(), eligible)


def test_candidate_source_requires_eligible_claims_object() -> None:
    source = _Source((_hit("clm_1", 1),))

    with pytest.raises(TypeError, match="eligible must be EligibleClaims"):
        run_candidate_source(source, _plan(), None)  # type: ignore[arg-type]
