from __future__ import annotations

import json
from pathlib import Path

from ms8.memory.application.replay import ClaimReplayView, ReplayState
from ms8.memory.domain.models import Claim, Evidence, ValidTime
from ms8.memory.infrastructure.graph_projection import GraphProjectionAdapter
from ms8.memory.retrieval.adapters import run_candidate_sources
from ms8.memory.retrieval.eligibility import EligibleClaims
from ms8.memory.retrieval.graph_sources import (
    GraphProjectionCandidateProvider,
    GraphProjectionCandidateSource,
)
from ms8.memory.retrieval.models import MemoryQuery, Principal, RetrievalPlan


def _claim(claim_id: str, *, subject: str, predicate: str) -> Claim:
    return Claim(
        claim_id=claim_id,
        kind="fact",
        text=f"{subject} {predicate}",
        subject=subject,
        predicate=predicate,
        value=True,
        scope="project:ms8",
        realm_id="realm:ms8",
        authority="user_explicit",
        sensitivity="internal",
        confidence=0.9,
        status="verified",
        valid_time=ValidTime(start="2026-07-01T00:00:00Z", basis="user_explicit"),
        created_from_event_id=f"event:{claim_id}",
    )


def _state() -> ReplayState:
    claim_a = _claim("claim:a", subject="MS8", predicate="memory_policy")
    claim_b = _claim("claim:b", subject="Retrieval", predicate="conflicts_with_ms8")
    claim_blocked = _claim("claim:blocked", subject="Secret", predicate="conflicts_with_ms8")
    claims = {
        claim.claim_id: ClaimReplayView(
            claim=claim,
            current_status="verified",
            decision_ids=(),
        )
        for claim in (claim_a, claim_b, claim_blocked)
    }
    evidence = {
        f"evidence:{claim_id}": Evidence(
            evidence_id=f"evidence:{claim_id}",
            claim_id=claim_id,
            event_id=f"event:{claim_id}",
            relation="supports",
            fragment={"claim_id": claim_id},
            quoted_text_hash=f"sha256:{claim_id}",
        )
        for claim_id in claims
    }
    return ReplayState(
        ledger_head="sha256:ledger",
        last_sequence=8,
        memory_events={},
        claims=claims,
        evidence=evidence,
        decisions={},
        conflicts={
            "conflict:one": {
                "claim_ids": ["claim:a", "claim:b", "claim:blocked"],
                "reason": "policy disagreement",
            }
        },
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
            text="MS8 policy",
            realm_ids=("realm:ms8",),
            scope="project:ms8",
        ),
        principal=principal,
        intent="project_rule",
        realm_ids=("realm:ms8",),
        entity_mentions=("MS8",),
    )


def test_graph_expansion_returns_only_eligible_evidence_backed_claims(tmp_path: Path) -> None:
    path = tmp_path / "graph.json"
    GraphProjectionAdapter(path).rebuild_from_state(_state())
    resolved: list[str] = []

    def resolve_evidence(claim_id: str) -> tuple[str, ...]:
        resolved.append(claim_id)
        return (f"evidence:{claim_id}",)

    source = GraphProjectionCandidateSource(
        GraphProjectionCandidateProvider(path, resolve_evidence, max_hops=2)
    )
    eligible = EligibleClaims(claim_ids=("claim:a", "claim:b"), evaluated_count=3)

    batch = run_candidate_sources((source,), _plan(), eligible)

    hits = batch.hits_by_source["graph-projection"]
    assert [item.claim_id for item in hits] == ["claim:b"]
    assert resolved == ["claim:b"]
    assert hits[0].reason["hop_count"] == 2
    assert hits[0].reason["seed_claim_id"] == "claim:a"
    assert hits[0].reason["path"] == (
        {
            "source": "claim:a",
            "relation": "involves_claim",
            "target": "conflict:one",
        },
        {
            "source": "conflict:one",
            "relation": "involves_claim",
            "target": "claim:b",
        },
    )


def test_graph_expansion_never_uses_blocked_claim_as_bridge(tmp_path: Path) -> None:
    path = tmp_path / "graph.json"
    GraphProjectionAdapter(path).rebuild_from_state(_state())
    resolved: list[str] = []
    provider = GraphProjectionCandidateProvider(
        path,
        lambda claim_id: resolved.append(claim_id) or (f"evidence:{claim_id}",),
    )

    records = provider(_plan(), ("claim:a",), 10)

    assert records == ()
    assert resolved == []


def test_graph_candidate_without_evidence_is_excluded(tmp_path: Path) -> None:
    path = tmp_path / "graph.json"
    GraphProjectionAdapter(path).rebuild_from_state(_state())
    provider = GraphProjectionCandidateProvider(path, lambda _claim_id: ())

    records = provider(_plan(), ("claim:a", "claim:b"), 10)

    assert records == ()


def test_invalid_graph_schema_degrades_inside_same_eligibility(tmp_path: Path) -> None:
    path = tmp_path / "graph.json"
    GraphProjectionAdapter(path).rebuild_from_state(_state())
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["manifest"]["schema"] = "invalid"
    path.write_text(json.dumps(payload), encoding="utf-8")
    source = GraphProjectionCandidateSource(
        GraphProjectionCandidateProvider(
            path,
            lambda claim_id: (f"evidence:{claim_id}",),
        )
    )
    eligible = EligibleClaims(claim_ids=("claim:a", "claim:b"), evaluated_count=2)

    batch = run_candidate_sources((source,), _plan(), eligible)

    assert batch.hits_by_source["graph-projection"] == ()
    assert batch.degradation_reasons == (
        "graph-projection:GraphProjectionFormatError",
    )
