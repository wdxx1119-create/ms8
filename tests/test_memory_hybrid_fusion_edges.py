from __future__ import annotations

from collections.abc import Mapping

import pytest

from ms8.memory.application.replay import ClaimReplayView, ReplayState
from ms8.memory.domain.models import Claim, Evidence, MemoryEvent, ValidTime
from ms8.memory.retrieval.adapters import CandidateBatch
from ms8.memory.retrieval.eligibility import EligibleClaims
from ms8.memory.retrieval.fusion import fuse_and_rerank
from ms8.memory.retrieval.models import CandidateHit, MemoryQuery, Principal, RetrievalPlan


def _claim(
    claim_id: str,
    *,
    authority: str,
    subject: str = "MS8",
    predicate: str = "retrieval_rule",
    start: str = "2026-01-01T00:00:00Z",
    end: str | None = None,
) -> Claim:
    return Claim(
        claim_id=claim_id,
        kind="fact",
        text=f"{subject} {predicate} {claim_id}",
        subject=subject,
        predicate=predicate,
        value=claim_id,
        scope="project:ms8",
        realm_id="realm:ms8",
        authority=authority,
        sensitivity="internal",
        confidence=0.9,
        status="verified",
        valid_time=ValidTime(start=start, end=end, basis="user_explicit"),
        created_from_event_id=f"event:{claim_id}",
    )


def _event(event_id: str, source: Mapping[str, object], observed_at: str) -> MemoryEvent:
    return MemoryEvent(
        event_id=event_id,
        kind="document_fragment",
        content={"text": event_id},
        source=source,
        observed_at=observed_at,
        trust_class="user_explicit",
    )


def _evidence(evidence_id: str, claim_id: str, event_id: str) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        claim_id=claim_id,
        event_id=event_id,
        relation="supports",
        fragment={"line": 1},
        quoted_text_hash=f"sha256:{evidence_id}",
    )


def _state(
    claims: tuple[Claim, ...],
    evidence: tuple[Evidence, ...],
    events: tuple[MemoryEvent, ...],
) -> ReplayState:
    return ReplayState(
        ledger_head="sha256:ledger",
        last_sequence=1,
        memory_events={item.event_id: item for item in events},
        claims={
            claim.claim_id: ClaimReplayView(
                claim=claim,
                current_status="verified",
                decision_ids=(),
            )
            for claim in claims
        },
        evidence={item.evidence_id: item for item in evidence},
        decisions={},
        conflicts={},
        logical_state_hash="sha256:state",
    )


def _plan() -> RetrievalPlan:
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
            realm_ids=("realm:ms8",),
            scope="project:ms8",
        ),
        principal=principal,
        intent="project_rule",
        realm_ids=("realm:ms8",),
    )


def _hit(
    claim_id: str,
    evidence_ids: tuple[str, ...],
    *,
    rank: int,
) -> CandidateHit:
    return CandidateHit(
        claim_id=claim_id,
        evidence_ids=evidence_ids,
        channel="lexical",
        rank=rank,
        raw_score=1.0,
        reason={"source": "lexical"},
    )


def _eligible(*claim_ids: str) -> EligibleClaims:
    return EligibleClaims(claim_ids=tuple(claim_ids), evaluated_count=len(claim_ids))


def test_locator_variants_from_one_document_count_as_one_evidence_source() -> None:
    claim = _claim("claim:one", authority="user_explicit")
    evidence = (
        _evidence("evidence:one", claim.claim_id, "event:one"),
        _evidence("evidence:two", claim.claim_id, "event:two"),
    )
    events = (
        _event(
            "event:one",
            {"path": "/docs/design.md", "line": 10, "chunk_index": 1},
            "2026-01-02T00:00:00Z",
        ),
        _event(
            "event:two",
            {"path": "/docs/design.md", "line": 80, "chunk_index": 7},
            "2026-01-02T00:00:00Z",
        ),
    )
    batch = CandidateBatch(
        hits_by_source={
            "lexical": (
                _hit(claim.claim_id, ("evidence:one", "evidence:two"), rank=1),
            )
        },
        traces=(),
    )

    ranked = fuse_and_rerank(
        batch,
        _state((claim,), evidence, events),
        _plan(),
        _eligible(claim.claim_id),
    ).ranked_claims[0]

    assert ranked.score_components["evidence_strength"] == pytest.approx(1.0 / 3.0)
    assert "evidence_independent_sources=1" in ranked.explanation[3]


def test_future_valid_until_does_not_advance_freshness_reference() -> None:
    claim = _claim(
        "claim:future-end",
        authority="user_explicit",
        start="2026-01-01T00:00:00Z",
        end="2099-01-01T00:00:00Z",
    )
    evidence = _evidence("evidence:future-end", claim.claim_id, "event:future-end")
    event = _event(
        "event:future-end",
        {"path": "/docs/current.md", "line": 1},
        "2026-01-02T00:00:00Z",
    )
    batch = CandidateBatch(
        hits_by_source={
            "lexical": (
                _hit(claim.claim_id, (evidence.evidence_id,), rank=1),
            )
        },
        traces=(),
    )

    ranked = fuse_and_rerank(
        batch,
        _state((claim,), (evidence,), (event,)),
        _plan(),
        _eligible(claim.claim_id),
    ).ranked_claims[0]

    assert ranked.score_components["type_freshness"] > 0.99


def test_authority_tier_and_blocker_trace_apply_only_on_real_collision() -> None:
    explicit = _claim("claim:explicit", authority="user_explicit")
    inferred = _claim("claim:inferred", authority="agent_inferred")
    other = _claim(
        "claim:other",
        authority="agent_inferred",
        subject="Other",
        predicate="unrelated",
    )
    evidence = (
        _evidence("evidence:explicit", explicit.claim_id, "event:explicit"),
        _evidence("evidence:inferred", inferred.claim_id, "event:inferred"),
        _evidence("evidence:other", other.claim_id, "event:other"),
    )
    events = tuple(
        _event(
            item.event_id,
            {"path": f"/{item.event_id}.md"},
            "2026-01-02T00:00:00Z",
        )
        for item in evidence
    )
    batch = CandidateBatch(
        hits_by_source={
            "lexical": (
                _hit(inferred.claim_id, ("evidence:inferred",), rank=1),
                _hit(other.claim_id, ("evidence:other",), rank=2),
                _hit(explicit.claim_id, ("evidence:explicit",), rank=100),
            )
        },
        traces=(),
    )

    result = fuse_and_rerank(
        batch,
        _state((explicit, inferred, other), evidence, events),
        _plan(),
        _eligible(explicit.claim_id, inferred.claim_id, other.claim_id),
    )
    by_id = {item.claim_id: item for item in result.ranked_claims}

    assert result.ranked_claims.index(by_id[explicit.claim_id]) < result.ranked_claims.index(
        by_id[inferred.claim_id]
    )
    assert by_id[inferred.claim_id].hard_rule_tier == 1
    assert (
        "authority_precedence=blocked_by:claim:explicit"
        in by_id[inferred.claim_id].explanation
    )
    assert by_id[other.claim_id].hard_rule_tier == 0
    assert not any(
        value.startswith("authority_precedence=")
        for value in by_id[other.claim_id].explanation
    )
