from __future__ import annotations

import pytest

from ms8.memory.retrieval import (
    CandidateHit,
    CandidateLimits,
    MemoryQuery,
    Principal,
    RankedClaim,
    RetrievalPlan,
    RetrievalTrace,
    TimeCoordinates,
)


def _principal() -> Principal:
    return Principal(
        principal_id="user:test",
        kind="user",
        realm_ids=("realm:project", "realm:personal"),
        scopes=("project", "personal"),
        allowed_sensitivities=("public", "internal", "private"),
        capabilities=("recall", "prepare_reply"),
    )


def _plan() -> RetrievalPlan:
    query = MemoryQuery(
        text="现在 MemoryCoreEngine 在哪里初始化？",
        purpose="prepare_reply",
        time=TimeCoordinates.from_as_of("2026-07-13T12:00:00+00:00"),
        realm_ids=("realm:project",),
        scope="project",
    )
    return RetrievalPlan(
        query=query,
        principal=_principal(),
        intent="code_symbol",
        realm_ids=("realm:project",),
        entity_mentions=("MemoryCoreEngine",),
        candidate_limits=CandidateLimits(lexical=80, vector=60, entity=40, temporal=20, graph=20),
        context_budget_tokens=1200,
    )


def test_as_of_expands_to_three_independent_time_coordinates() -> None:
    coordinates = TimeCoordinates.from_as_of("2026-07-13T21:00:00+09:00")

    assert coordinates.recorded_as_of == "2026-07-13T12:00:00Z"
    assert coordinates.observed_as_of == "2026-07-13T12:00:00Z"
    assert coordinates.valid_at == "2026-07-13T12:00:00Z"


def test_time_coordinates_require_timezone() -> None:
    with pytest.raises(ValueError, match="must include a timezone"):
        TimeCoordinates(valid_at="2026-07-13T12:00:00")


def test_principal_requires_at_least_one_realm() -> None:
    with pytest.raises(ValueError, match="realm_ids must not be empty"):
        Principal(principal_id="user:test", kind="user", realm_ids=())


def test_plan_cannot_expand_beyond_principal_realms() -> None:
    query = MemoryQuery(text="test", realm_ids=("realm:other",))

    with pytest.raises(ValueError, match="subset of principal.realm_ids"):
        RetrievalPlan(
            query=query,
            principal=_principal(),
            intent="open_recall",
            realm_ids=("realm:other",),
        )


def test_candidate_hit_is_claim_based_and_reason_is_immutable() -> None:
    hit = CandidateHit(
        claim_id="clm_1",
        evidence_ids=("evd_1",),
        channel="lexical",
        rank=1,
        raw_score=3.5,
        reason={"matched_terms": ["memorycoreengine"]},
    )

    assert hit.claim_id == "clm_1"
    assert hit.evidence_ids == ("evd_1",)
    with pytest.raises(TypeError):
        hit.reason["new"] = True  # type: ignore[index]


def test_candidate_limits_validate_each_channel() -> None:
    limits = CandidateLimits(lexical=10, vector=20, entity=30, temporal=40, graph=50)

    assert limits.for_channel("graph") == 50
    with pytest.raises(ValueError, match="positive integer"):
        CandidateLimits(vector=0)


def test_ranked_claim_freezes_score_components() -> None:
    ranked = RankedClaim(
        claim_id="clm_1",
        evidence_ids=("evd_1",),
        score=0.91,
        hard_rule_tier=0,
        score_components={"rrf": 0.7, "authority": 1.0},
        explanation=("authorized", "evidence_available"),
    )

    assert ranked.score_components["authority"] == 1.0
    with pytest.raises(TypeError):
        ranked.score_components["rrf"] = 0.0  # type: ignore[index]


def test_retrieval_trace_preserves_structured_counts() -> None:
    trace = RetrievalTrace(
        plan=_plan(),
        eligible_claim_count=12,
        blocked_reasons={"realm_mismatch": 2, "inactive_status": 1},
        source_hit_counts={"lexical": 8, "vector": 5},
        degradation_reasons=("graph_projection_unavailable",),
    )

    assert dict(trace.blocked_reasons) == {"inactive_status": 1, "realm_mismatch": 2}
    assert trace.source_hit_counts["lexical"] == 8
    assert trace.plan.query.time.valid_at == "2026-07-13T12:00:00Z"
