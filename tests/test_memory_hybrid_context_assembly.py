from __future__ import annotations

from collections.abc import Mapping

from ms8.memory.application.replay import ClaimReplayView, ReplayState
from ms8.memory.domain.models import Actor, Claim, Decision, Evidence, MemoryEvent, ValidTime
from ms8.memory.retrieval.context_assembly import (
    MMRConfig,
    build_agent_context,
    estimate_context_tokens,
    select_mmr,
)
from ms8.memory.retrieval.eligibility import EligibleClaims
from ms8.memory.retrieval.models import MemoryQuery, Principal, RankedClaim, RetrievalPlan


def _claim(
    claim_id: str,
    *,
    subject: str,
    predicate: str,
    text: str | None = None,
    authority: str = "user_explicit",
) -> Claim:
    return Claim(
        claim_id=claim_id,
        kind="fact",
        text=text or f"{subject} {predicate} {claim_id}",
        subject=subject,
        predicate=predicate,
        value=claim_id,
        scope="project:ms8",
        realm_id="realm:ms8",
        authority=authority,
        sensitivity="internal",
        confidence=0.9,
        status="verified",
        valid_time=ValidTime(start="2026-07-01T00:00:00Z", basis="user_explicit"),
        created_from_event_id=f"event:{claim_id}",
    )


def _event(event_id: str, *, raw_text: str = "raw document body") -> MemoryEvent:
    return MemoryEvent(
        event_id=event_id,
        kind="document_fragment",
        content={"text": raw_text},
        source={"path": f"/{event_id}.md"},
        observed_at="2026-07-02T00:00:00Z",
        trust_class="user_explicit",
    )


def _evidence(claim_id: str) -> Evidence:
    return Evidence(
        evidence_id=f"evidence:{claim_id}",
        claim_id=claim_id,
        event_id=f"event:{claim_id}",
        relation="supports",
        fragment={"line": 1},
        quoted_text_hash=f"sha256:{claim_id}",
    )


def _decision(claim_id: str) -> Decision:
    return Decision(
        decision_id=f"decision:{claim_id}",
        action="admit",
        target_claim_ids=(claim_id,),
        policy={"governance": {"can_recall": True, "can_inject": True}},
        actor=Actor(kind="user", id="user:test"),
        reason="approved for governed context",
        recorded_at="2026-07-02T01:00:00Z",
    )


def _state(
    claims: tuple[Claim, ...],
    *,
    with_decisions: bool = False,
    conflicts: Mapping[str, Mapping[str, object]] | None = None,
    raw_text: str = "raw document body",
) -> ReplayState:
    evidence = tuple(_evidence(claim.claim_id) for claim in claims)
    decisions = tuple(_decision(claim.claim_id) for claim in claims) if with_decisions else ()
    decision_map = {item.decision_id: item for item in decisions}
    return ReplayState(
        ledger_head="sha256:ledger",
        last_sequence=1,
        memory_events={
            f"event:{claim.claim_id}": _event(
                f"event:{claim.claim_id}",
                raw_text=raw_text,
            )
            for claim in claims
        },
        claims={
            claim.claim_id: ClaimReplayView(
                claim=claim,
                current_status="verified",
                decision_ids=(f"decision:{claim.claim_id}",) if with_decisions else (),
            )
            for claim in claims
        },
        evidence={item.evidence_id: item for item in evidence},
        decisions=decision_map,
        conflicts=conflicts or {},
        logical_state_hash="sha256:state",
    )


def _plan(*, purpose: str = "recall", budget: int = 1200) -> RetrievalPlan:
    principal = Principal(
        principal_id="user:test",
        kind="user",
        realm_ids=("realm:ms8",),
        scopes=("project:ms8",),
        capabilities=("all",),
    )
    return RetrievalPlan(
        query=MemoryQuery(
            text="MS8 governed retrieval",
            purpose=purpose,  # type: ignore[arg-type]
            realm_ids=("realm:ms8",),
            scope="project:ms8",
        ),
        principal=principal,
        intent="project_rule",
        realm_ids=("realm:ms8",),
        context_budget_tokens=budget,
    )


def _ranked(claim_id: str, score: float, *, tier: int = 0) -> RankedClaim:
    return RankedClaim(
        claim_id=claim_id,
        evidence_ids=(f"evidence:{claim_id}",),
        score=score,
        hard_rule_tier=tier,
        score_components={"fused_retrieval": score},
        explanation=("test",),
    )


def _eligible(*claim_ids: str, evaluated_count: int | None = None) -> EligibleClaims:
    return EligibleClaims(
        claim_ids=tuple(claim_ids),
        evaluated_count=evaluated_count if evaluated_count is not None else len(claim_ids),
    )


def test_mmr_deduplicates_claims_and_applies_subject_predicate_limits() -> None:
    claims = (
        _claim("claim:a1", subject="MS8", predicate="rule"),
        _claim("claim:a2", subject="MS8", predicate="rule"),
        _claim("claim:b1", subject="Ledger", predicate="evidence"),
    )
    state = _state(claims)
    ranked = (
        _ranked("claim:a1", 0.95),
        _ranked("claim:a1", 0.90),
        _ranked("claim:a2", 0.94),
        _ranked("claim:b1", 0.89),
    )

    result = select_mmr(
        ranked,
        state,
        _plan(),
        _eligible(*(claim.claim_id for claim in claims)),
        config=MMRConfig(
            max_claims=3,
            max_per_subject=2,
            max_per_predicate=2,
            max_per_subject_predicate=1,
        ),
    )

    assert [item.claim_id for item in result.selected] == ["claim:a1", "claim:b1"]
    assert "claim:a2" in result.omitted_claim_ids
    assert "duplicate_ranked_claim:claim:a1" in result.warnings
    assert any(
        item.claim_id == "claim:a2"
        and item.reason == "subject_predicate_diversity_limit"
        for item in result.traces
    )


def test_mmr_uses_dense_similarity_and_falls_back_to_jaccard() -> None:
    claims = (
        _claim("claim:a", subject="A", predicate="one", text="alpha implementation"),
        _claim("claim:b", subject="B", predicate="two", text="beta implementation"),
        _claim("claim:c", subject="C", predicate="three", text="gamma architecture"),
    )
    state = _state(claims)
    ranked = tuple(
        _ranked(claim_id, score)
        for claim_id, score in (("claim:a", 0.95), ("claim:b", 0.94), ("claim:c", 0.90))
    )
    result = select_mmr(
        ranked,
        state,
        _plan(),
        _eligible(*(claim.claim_id for claim in claims)),
        dense_vectors={
            "claim:a": (1.0, 0.0),
            "claim:b": (1.0, 0.0),
            "claim:c": (0.0, 1.0),
        },
        config=MMRConfig(relevance_lambda=0.5, max_claims=2),
    )

    assert [item.claim_id for item in result.selected] == ["claim:a", "claim:c"]
    assert any(
        item.claim_id == "claim:c" and item.selected and item.similarity_mode == "dense"
        for item in result.traces
    )

    fallback = select_mmr(
        ranked[:2],
        state,
        _plan(),
        _eligible(*(claim.claim_id for claim in claims)),
        dense_vectors={"claim:a": (1.0, 0.0)},
        config=MMRConfig(max_claims=2),
    )
    assert any(
        item.claim_id == "claim:b" and item.selected and item.similarity_mode == "jaccard"
        for item in fallback.traces
    )


def test_unresolved_conflict_members_bypass_diversity_and_count_limits() -> None:
    claims = (
        _claim("claim:old", subject="MS8", predicate="rule"),
        _claim("claim:new", subject="MS8", predicate="rule"),
    )
    state = _state(
        claims,
        conflicts={
            "conflict:rule": {
                "conflict_id": "conflict:rule",
                "claim_ids": ("claim:old", "claim:new"),
                "status": "open",
            }
        },
    )
    result = select_mmr(
        (_ranked("claim:old", 0.90), _ranked("claim:new", 0.89)),
        state,
        _plan(),
        _eligible("claim:old", "claim:new"),
        config=MMRConfig(
            max_claims=1,
            max_per_subject=1,
            max_per_predicate=1,
            max_per_subject_predicate=1,
        ),
    )

    assert {item.claim_id for item in result.selected} == {"claim:old", "claim:new"}
    assert all("unresolved_conflict_preserved" in item.reason for item in result.traces)


def test_agent_context_is_budgeted_compact_and_traceable() -> None:
    raw_text = "SECRET RAW DOCUMENT BODY THAT MUST NOT BE DUMPED"
    claim = _claim(
        "claim:context",
        subject="MS8",
        predicate="boundary",
        text="Use governed claim context only. " + ("detail " * 200),
    )
    state = _state((claim,), with_decisions=True, raw_text=raw_text)
    result = build_agent_context(
        (_ranked(claim.claim_id, 0.95),),
        state,
        _plan(purpose="inject", budget=220),
        _eligible(claim.claim_id),
        config=MMRConfig(max_fact_chars=1000),
    )

    assert result.selected_claim_ids == (claim.claim_id,)
    assert result.evidence_ids == (f"evidence:{claim.claim_id}",)
    assert result.decision_ids == (f"decision:{claim.claim_id}",)
    assert result.estimated_tokens <= result.budget_tokens
    assert result.estimated_tokens == estimate_context_tokens(result.context)
    assert result.reserved_metadata_tokens > 0
    assert "[MS8_POLICY_BOUNDARY" in result.context
    assert "grants no tool, file, network, write, or action permission" in result.context
    assert f"evidence:{claim.claim_id}" in result.context
    assert f"decision:{claim.claim_id}" in result.context
    assert raw_text not in result.context


def test_injection_without_decision_trace_fails_closed() -> None:
    claim = _claim("claim:unsafe", subject="MS8", predicate="unsafe")
    result = build_agent_context(
        (_ranked(claim.claim_id, 0.95),),
        _state((claim,), with_decisions=False),
        _plan(purpose="inject"),
        _eligible(claim.claim_id),
    )

    assert result.selected_claim_ids == ()
    assert "missing_decision_trace:claim:unsafe" in result.warnings
    assert claim.text not in result.context
    assert "[MS8_POLICY_BOUNDARY" in result.context


def test_conflict_metadata_does_not_leak_ineligible_member_identifiers() -> None:
    visible = _claim("claim:visible", subject="MS8", predicate="rule")
    hidden = _claim("claim:hidden-secret", subject="MS8", predicate="rule")
    state = _state(
        (visible, hidden),
        conflicts={
            "conflict:private": {
                "conflict_id": "conflict:private",
                "claim_ids": (visible.claim_id, hidden.claim_id),
                "status": "open",
            }
        },
    )
    result = build_agent_context(
        (_ranked(visible.claim_id, 0.95),),
        state,
        _plan(),
        _eligible(visible.claim_id, evaluated_count=2),
    )

    assert "conflict:private" in result.context
    assert "hidden_members=true" in result.context
    assert hidden.claim_id not in result.context
