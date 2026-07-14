from __future__ import annotations

from collections.abc import Mapping

import pytest

from ms8.memory.application.replay import ClaimReplayView, ReplayState
from ms8.memory.domain.models import Claim, Evidence, MemoryEvent, ValidTime
from ms8.memory.retrieval.adapters import CandidateBatch
from ms8.memory.retrieval.eligibility import EligibleClaims
from ms8.memory.retrieval.fusion import (
    FUSION_CONFIG_SCHEMA,
    FusionConfig,
    fuse_and_rerank,
)
from ms8.memory.retrieval.models import CandidateHit, MemoryQuery, Principal, RetrievalPlan


def _claim(
    claim_id: str,
    *,
    authority: str = "user_explicit",
    status: str = "verified",
    subject: str = "MS8",
    predicate: str = "retrieval_rule",
    kind: str = "fact",
    start: str = "2026-07-01T00:00:00Z",
) -> Claim:
    return Claim(
        claim_id=claim_id,
        kind=kind,
        text=f"{subject} {predicate} {claim_id}",
        subject=subject,
        predicate=predicate,
        value=claim_id,
        scope="project:ms8",
        realm_id="realm:ms8",
        authority=authority,
        sensitivity="internal",
        confidence=0.9,
        status=status,
        valid_time=ValidTime(start=start, basis="user_explicit"),
        created_from_event_id=f"event:{claim_id}",
    )


def _event(
    event_id: str,
    *,
    source: Mapping[str, object],
    observed_at: str = "2026-07-02T00:00:00Z",
) -> MemoryEvent:
    return MemoryEvent(
        event_id=event_id,
        kind="document_fragment",
        content={"text": event_id},
        source=source,
        observed_at=observed_at,
        trust_class="user_explicit",
    )


def _state(
    claims: tuple[Claim, ...],
    evidence: tuple[Evidence, ...],
    *,
    events: tuple[MemoryEvent, ...] = (),
    conflicts: Mapping[str, Mapping[str, object]] | None = None,
) -> ReplayState:
    return ReplayState(
        ledger_head="sha256:ledger",
        last_sequence=1,
        memory_events={event.event_id: event for event in events},
        claims={
            claim.claim_id: ClaimReplayView(
                claim=claim,
                current_status=claim.status,
                decision_ids=(),
            )
            for claim in claims
        },
        evidence={item.evidence_id: item for item in evidence},
        decisions={},
        conflicts=conflicts or {},
        logical_state_hash="sha256:state",
    )


def _evidence(
    evidence_id: str,
    claim_id: str,
    event_id: str,
    *,
    relation: str = "supports",
    weight: float = 1.0,
) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        claim_id=claim_id,
        event_id=event_id,
        relation=relation,
        fragment={"source_id": event_id, "line": 1},
        quoted_text_hash=f"sha256:{evidence_id}",
        weight=weight,
    )


def _plan(*, purpose: str = "recall", intent: str = "project_rule") -> RetrievalPlan:
    principal = Principal(
        principal_id="user:test",
        kind="user",
        realm_ids=("realm:ms8",),
        scopes=("project:ms8",),
        capabilities=("all",),
    )
    return RetrievalPlan(
        query=MemoryQuery(
            text="MS8 retrieval rule",
            purpose=purpose,  # type: ignore[arg-type]
            realm_ids=("realm:ms8",),
            scope="project:ms8",
        ),
        principal=principal,
        intent=intent,  # type: ignore[arg-type]
        realm_ids=("realm:ms8",),
    )


def _hit(
    claim_id: str,
    *,
    evidence_ids: tuple[str, ...],
    channel: str,
    rank: int,
    raw_score: float = 1.0,
    source: str,
) -> CandidateHit:
    return CandidateHit(
        claim_id=claim_id,
        evidence_ids=evidence_ids,
        channel=channel,  # type: ignore[arg-type]
        rank=rank,
        raw_score=raw_score,
        reason={"source": source},
    )


def _eligible(*claim_ids: str) -> EligibleClaims:
    return EligibleClaims(claim_ids=tuple(claim_ids), evaluated_count=len(claim_ids))


def test_weighted_rrf_deduplicates_claim_and_unions_evidence() -> None:
    claim = _claim("claim:a")
    e1 = _evidence("evidence:1", claim.claim_id, "event:1")
    e2 = _evidence("evidence:2", claim.claim_id, "event:2")
    state = _state(
        (claim,),
        (e1, e2),
        events=(
            _event("event:1", source={"path": "/one.md"}),
            _event("event:2", source={"path": "/two.md"}),
        ),
    )
    batch = CandidateBatch(
        hits_by_source={
            "lexical": (
                _hit(
                    claim.claim_id,
                    evidence_ids=(e1.evidence_id,),
                    channel="lexical",
                    rank=1,
                    source="lexical",
                ),
            ),
            "vector": (
                _hit(
                    claim.claim_id,
                    evidence_ids=(e2.evidence_id,),
                    channel="vector",
                    rank=2,
                    source="vector",
                ),
            ),
        },
        traces=(),
    )

    result = fuse_and_rerank(batch, state, _plan(), _eligible(claim.claim_id))

    assert result.config_schema == FUSION_CONFIG_SCHEMA
    assert result.candidate_count == 1
    assert result.ranked_claims[0].claim_id == claim.claim_id
    assert result.ranked_claims[0].evidence_ids == ("evidence:1", "evidence:2")
    assert result.ranked_claims[0].score_components["rrf_raw"] > 0.0
    assert "lexical:lexical@1" in result.ranked_claims[0].explanation[1]
    assert "vector:vector@2" in result.ranked_claims[0].explanation[1]


def test_duplicate_chunks_from_one_source_do_not_inflate_evidence_strength() -> None:
    claim_a = _claim("claim:a")
    claim_b = _claim("claim:b")
    evidence = (
        _evidence("evidence:a1", claim_a.claim_id, "event:a1"),
        _evidence("evidence:a2", claim_a.claim_id, "event:a2"),
        _evidence("evidence:b1", claim_b.claim_id, "event:b1"),
    )
    events = (
        _event("event:a1", source={"path": "/same.md"}),
        _event("event:a2", source={"path": "/same.md"}),
        _event("event:b1", source={"path": "/same.md"}),
    )
    state = _state((claim_a, claim_b), evidence, events=events)
    batch = CandidateBatch(
        hits_by_source={
            "lexical": (
                _hit(
                    claim_a.claim_id,
                    evidence_ids=("evidence:a1", "evidence:a2"),
                    channel="lexical",
                    rank=1,
                    source="lexical",
                ),
                _hit(
                    claim_b.claim_id,
                    evidence_ids=("evidence:b1",),
                    channel="lexical",
                    rank=1,
                    source="lexical",
                ),
            )
        },
        traces=(),
    )

    result = fuse_and_rerank(
        batch,
        state,
        _plan(),
        _eligible(claim_a.claim_id, claim_b.claim_id),
    )
    by_id = {item.claim_id: item for item in result.ranked_claims}

    assert (
        by_id[claim_a.claim_id].score_components["evidence_strength"]
        == by_id[claim_b.claim_id].score_components["evidence_strength"]
    )
    assert "evidence_independent_sources=1" in by_id[claim_a.claim_id].explanation[3]


def test_authority_precedence_blocks_agent_inference_for_same_predicate() -> None:
    explicit = _claim("claim:explicit", authority="user_explicit", status="accepted")
    inferred = _claim("claim:inferred", authority="agent_inferred", status="verified")
    e1 = _evidence("evidence:explicit", explicit.claim_id, "event:explicit")
    e2 = _evidence("evidence:inferred", inferred.claim_id, "event:inferred")
    state = _state(
        (explicit, inferred),
        (e1, e2),
        events=(
            _event("event:explicit", source={"path": "/explicit.md"}),
            _event("event:inferred", source={"path": "/inferred.md"}),
        ),
    )
    batch = CandidateBatch(
        hits_by_source={
            "lexical": (
                _hit(
                    inferred.claim_id,
                    evidence_ids=(e2.evidence_id,),
                    channel="lexical",
                    rank=1,
                    source="lexical",
                ),
                _hit(
                    explicit.claim_id,
                    evidence_ids=(e1.evidence_id,),
                    channel="lexical",
                    rank=100,
                    source="lexical",
                ),
            ),
            "vector": (
                _hit(
                    inferred.claim_id,
                    evidence_ids=(e2.evidence_id,),
                    channel="vector",
                    rank=1,
                    source="vector",
                ),
            ),
        },
        traces=(),
    )

    result = fuse_and_rerank(
        batch,
        state,
        _plan(),
        _eligible(explicit.claim_id, inferred.claim_id),
    )

    assert [item.claim_id for item in result.ranked_claims] == [
        explicit.claim_id,
        inferred.claim_id,
    ]
    assert result.ranked_claims[1].score > result.ranked_claims[0].score
    assert result.ranked_claims[0].hard_rule_tier == 0
    assert result.ranked_claims[1].hard_rule_tier == 1


def test_stable_tie_breaking_is_independent_of_input_order() -> None:
    claim_a = _claim("claim:a")
    claim_b = _claim("claim:b")
    evidence = (
        _evidence("evidence:a", claim_a.claim_id, "event:a"),
        _evidence("evidence:b", claim_b.claim_id, "event:b"),
    )
    state = _state(
        (claim_a, claim_b),
        evidence,
        events=(
            _event("event:a", source={"path": "/a.md"}),
            _event("event:b", source={"path": "/b.md"}),
        ),
    )
    first = CandidateBatch(
        hits_by_source={
            "vector": (
                _hit(
                    claim_b.claim_id,
                    evidence_ids=("evidence:b",),
                    channel="vector",
                    rank=1,
                    source="vector",
                ),
                _hit(
                    claim_a.claim_id,
                    evidence_ids=("evidence:a",),
                    channel="vector",
                    rank=1,
                    source="vector",
                ),
            )
        },
        traces=(),
    )
    second = CandidateBatch(
        hits_by_source={
            "vector": tuple(reversed(first.hits_by_source["vector"])),
        },
        traces=(),
    )
    eligible = _eligible(claim_a.claim_id, claim_b.claim_id)

    first_ids = [
        item.claim_id
        for item in fuse_and_rerank(first, state, _plan(), eligible).ranked_claims
    ]
    second_ids = [
        item.claim_id
        for item in fuse_and_rerank(second, state, _plan(), eligible).ranked_claims
    ]

    assert first_ids == second_ids == ["claim:a", "claim:b"]


def test_unresolved_conflict_is_preserved_and_explained() -> None:
    conflicted = _claim("claim:conflicted")
    clean = _claim("claim:clean")
    evidence = (
        _evidence("evidence:conflicted", conflicted.claim_id, "event:conflicted"),
        _evidence("evidence:clean", clean.claim_id, "event:clean"),
    )
    state = _state(
        (conflicted, clean),
        evidence,
        events=(
            _event("event:conflicted", source={"path": "/conflicted.md"}),
            _event("event:clean", source={"path": "/clean.md"}),
        ),
        conflicts={
            "conflict:one": {
                "claim_ids": [conflicted.claim_id],
                "status": "open",
            }
        },
    )
    batch = CandidateBatch(
        hits_by_source={
            "lexical": (
                _hit(
                    conflicted.claim_id,
                    evidence_ids=("evidence:conflicted",),
                    channel="lexical",
                    rank=1,
                    source="lexical",
                ),
                _hit(
                    clean.claim_id,
                    evidence_ids=("evidence:clean",),
                    channel="lexical",
                    rank=1,
                    source="lexical",
                ),
            )
        },
        traces=(),
    )

    result = fuse_and_rerank(
        batch,
        state,
        _plan(),
        _eligible(conflicted.claim_id, clean.claim_id),
    )
    by_id = {item.claim_id: item for item in result.ranked_claims}

    assert set(by_id) == {conflicted.claim_id, clean.claim_id}
    assert by_id[conflicted.claim_id].score_components["conflict_handling"] == 0.25
    assert "conflicts=conflict:one" in by_id[conflicted.claim_id].explanation


def test_candidate_outside_eligibility_fails_closed() -> None:
    allowed = _claim("claim:allowed")
    blocked = _claim("claim:blocked")
    evidence = (
        _evidence("evidence:allowed", allowed.claim_id, "event:allowed"),
        _evidence("evidence:blocked", blocked.claim_id, "event:blocked"),
    )
    state = _state((allowed, blocked), evidence)
    batch = CandidateBatch(
        hits_by_source={
            "lexical": (
                _hit(
                    blocked.claim_id,
                    evidence_ids=("evidence:blocked",),
                    channel="lexical",
                    rank=1,
                    source="lexical",
                ),
            )
        },
        traces=(),
    )

    with pytest.raises(PermissionError, match="outside the retrieval eligibility set"):
        fuse_and_rerank(batch, state, _plan(), _eligible(allowed.claim_id))


def test_missing_or_cross_claim_evidence_fails_closed() -> None:
    claim_a = _claim("claim:a")
    claim_b = _claim("claim:b")
    evidence = _evidence("evidence:b", claim_b.claim_id, "event:b")
    state = _state((claim_a, claim_b), (evidence,))
    batch = CandidateBatch(
        hits_by_source={
            "lexical": (
                _hit(
                    claim_a.claim_id,
                    evidence_ids=(evidence.evidence_id,),
                    channel="lexical",
                    rank=1,
                    source="lexical",
                ),
            )
        },
        traces=(),
    )

    with pytest.raises(ValueError, match="belongs to another claim"):
        fuse_and_rerank(batch, state, _plan(), _eligible(claim_a.claim_id))


def test_custom_channel_weights_are_versioned_and_deterministic() -> None:
    lexical = _claim("claim:lexical")
    vector = _claim("claim:vector")
    evidence = (
        _evidence("evidence:lexical", lexical.claim_id, "event:lexical"),
        _evidence("evidence:vector", vector.claim_id, "event:vector"),
    )
    state = _state(
        (lexical, vector),
        evidence,
        events=(
            _event("event:lexical", source={"path": "/lexical.md"}),
            _event("event:vector", source={"path": "/vector.md"}),
        ),
    )
    batch = CandidateBatch(
        hits_by_source={
            "lexical": (
                _hit(
                    lexical.claim_id,
                    evidence_ids=("evidence:lexical",),
                    channel="lexical",
                    rank=1,
                    source="lexical",
                ),
            ),
            "vector": (
                _hit(
                    vector.claim_id,
                    evidence_ids=("evidence:vector",),
                    channel="vector",
                    rank=1,
                    source="vector",
                ),
            ),
        },
        traces=(),
    )
    config = FusionConfig(
        channel_weights={
            "lexical": 2.0,
            "vector": 0.5,
            "entity": 0.9,
            "temporal": 1.1,
            "graph": 0.8,
        }
    )

    result = fuse_and_rerank(
        batch,
        state,
        _plan(),
        _eligible(lexical.claim_id, vector.claim_id),
        config=config,
    )

    assert result.ranked_claims[0].claim_id == lexical.claim_id
    assert config.to_dict()["schema"] == FUSION_CONFIG_SCHEMA
    with pytest.raises(ValueError, match="unsupported fusion config schema"):
        FusionConfig(schema="ms8.hybrid_fusion.v2")
