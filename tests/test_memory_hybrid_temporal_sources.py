from __future__ import annotations

import pytest

from ms8.memory.application.replay import ClaimReplayView, ReplayState
from ms8.memory.domain.models import Claim, Evidence, ValidTime
from ms8.memory.retrieval.adapters import run_candidate_sources
from ms8.memory.retrieval.candidate_sources import CandidateSourceError
from ms8.memory.retrieval.eligibility import EligibleClaims
from ms8.memory.retrieval.models import MemoryQuery, Principal, RetrievalPlan
from ms8.memory.retrieval.temporal_sources import (
    TemporalReplayCandidateProvider,
    TemporalReplayCandidateSource,
)


def _claim(
    claim_id: str,
    *,
    text: str,
    status: str,
    basis: str,
    start: str,
    end: str | None = None,
) -> Claim:
    return Claim(
        claim_id=claim_id,
        kind="fact",
        text=text,
        subject="MS8",
        predicate="retrieval_rule",
        value=text,
        scope="project:ms8",
        realm_id="realm:ms8",
        authority="user_explicit",
        sensitivity="internal",
        confidence=0.9,
        status=status,
        valid_time=ValidTime(start=start, end=end, basis=basis),
        created_from_event_id=f"event:{claim_id}",
    )


def _state() -> ReplayState:
    current = _claim(
        "claim:current",
        text="MS8 current rule uses hybrid retrieval",
        status="verified",
        basis="user_explicit",
        start="2026-07-01T00:00:00Z",
        end="2027-01-01T00:00:00Z",
    )
    unknown = _claim(
        "claim:unknown",
        text="MS8 current rule has a fallback",
        status="accepted",
        basis="unknown",
        start="2026-07-01T00:00:00Z",
    )
    historical = _claim(
        "claim:historical",
        text="MS8 old rule used lexical only",
        status="superseded",
        basis="user_explicit",
        start="2026-01-01T00:00:00Z",
        end="2026-07-01T00:00:00Z",
    )
    claims = {
        current.claim_id: ClaimReplayView(
            claim=current,
            current_status="verified",
            decision_ids=(),
        ),
        unknown.claim_id: ClaimReplayView(
            claim=unknown,
            current_status="accepted",
            decision_ids=(),
        ),
        historical.claim_id: ClaimReplayView(
            claim=historical,
            current_status="superseded",
            decision_ids=(),
        ),
    }
    evidence = {
        f"evidence:{claim_id}": Evidence(
            evidence_id=f"evidence:{claim_id}",
            claim_id=claim_id,
            event_id=f"event:{claim_id}",
            relation="supports",
            fragment={"source": claim_id},
            quoted_text_hash=f"sha256:{claim_id}",
        )
        for claim_id in claims
    }
    return ReplayState(
        ledger_head="sha256:ledger",
        last_sequence=6,
        memory_events={},
        claims=claims,
        evidence=evidence,
        decisions={},
        conflicts={},
        logical_state_hash="sha256:state",
    )


def _plan(*, text: str, purpose: str, intent: str) -> RetrievalPlan:
    principal = Principal(
        principal_id="user:test",
        kind="user",
        realm_ids=("realm:ms8",),
        scopes=("project:ms8",),
        capabilities=("all",),
    )
    return RetrievalPlan(
        query=MemoryQuery(
            text=text,
            purpose=purpose,  # type: ignore[arg-type]
            realm_ids=("realm:ms8",),
            scope="project:ms8",
        ),
        principal=principal,
        intent=intent,  # type: ignore[arg-type]
        realm_ids=("realm:ms8",),
    )


def test_current_temporal_retrieval_excludes_superseded_claims() -> None:
    state = _state()
    source = TemporalReplayCandidateSource(TemporalReplayCandidateProvider(state))
    eligible = EligibleClaims(
        claim_ids=("claim:current", "claim:unknown", "claim:historical"),
        evaluated_count=3,
    )

    batch = run_candidate_sources(
        (source,),
        _plan(text="MS8 current rule", purpose="recall", intent="current_state"),
        eligible,
    )

    hits = batch.hits_by_source["temporal-replay"]
    assert [item.claim_id for item in hits] == ["claim:current", "claim:unknown"]
    assert hits[0].reason["temporal_mode"] == "current"
    assert hits[0].reason["valid_until"] == "2027-01-01T00:00:00Z"
    assert hits[1].reason["supplementary"] is True
    assert hits[0].raw_score > hits[1].raw_score


def test_temporal_retrieval_rejects_single_generic_match_in_broad_question() -> None:
    retention = _claim(
        "claim:retention",
        text="Backup retention is 30 days",
        status="accepted",
        basis="user_explicit",
        start="2026-07-01T00:00:00Z",
    )
    release = _claim(
        "claim:release",
        text="Current release policy requires all checks",
        status="verified",
        basis="user_explicit",
        start="2026-07-01T00:00:00Z",
    )
    claims = {
        claim.claim_id: ClaimReplayView(
            claim=claim,
            current_status=claim.status,
            decision_ids=(),
        )
        for claim in (retention, release)
    }
    state = ReplayState(
        ledger_head="sha256:ledger",
        last_sequence=4,
        memory_events={},
        claims=claims,
        evidence={
            f"evidence:{claim_id}": Evidence(
                evidence_id=f"evidence:{claim_id}",
                claim_id=claim_id,
                event_id=f"event:{claim_id}",
                relation="supports",
                fragment={"source": claim_id},
                quoted_text_hash=f"sha256:{claim_id}",
            )
            for claim_id in claims
        },
        decisions={},
        conflicts={},
        logical_state_hash="sha256:state",
    )
    provider = TemporalReplayCandidateProvider(state)

    records = provider(
        _plan(
            text="What is the backup retention policy?",
            purpose="recall",
            intent="project_rule",
        ),
        ("claim:release", "claim:retention"),
        10,
    )

    assert [item.claim_id for item in records] == ["claim:retention"]
    assert records[0].reason["matched_terms"] == ("backup", "retention")
    assert records[0].reason["informative_query_term_count"] == 3


def test_historical_temporal_retrieval_requires_explicit_historical_mode() -> None:
    state = _state()
    provider = TemporalReplayCandidateProvider(state)
    eligible_ids = ("claim:current", "claim:historical")

    historical = provider(
        _plan(
            text="why MS8 old rule",
            purpose="historical",
            intent="historical_reason",
        ),
        eligible_ids,
        10,
    )
    current = provider(
        _plan(text="MS8 old rule", purpose="recall", intent="current_state"),
        eligible_ids,
        10,
    )

    assert [item.claim_id for item in historical] == ["claim:historical"]
    assert historical[0].reason["temporal_mode"] == "historical"
    assert historical[0].reason["valid_until"] == "2026-07-01T00:00:00Z"
    assert current == ()


def test_temporal_provider_missing_eligible_claim_fails_closed() -> None:
    state = _state()
    source = TemporalReplayCandidateSource(TemporalReplayCandidateProvider(state))
    eligible = EligibleClaims(claim_ids=("claim:missing",), evaluated_count=1)

    with pytest.raises(CandidateSourceError, match="missing claim"):
        run_candidate_sources(
            (source,),
            _plan(text="MS8", purpose="recall", intent="current_state"),
            eligible,
        )
