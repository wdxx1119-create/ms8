from __future__ import annotations

import json
from pathlib import Path

from ms8.memory.application.replay import ClaimReplayView, ReplayState
from ms8.memory.domain.models import Claim, Evidence, ValidTime
from ms8.memory.infrastructure.search_projection import (
    SEARCH_BUILDER_VERSION,
    SEARCH_PROJECTION_SCHEMA,
    SearchProjectionAdapter,
)
from ms8.memory.retrieval.models import MemoryQuery, Principal, RetrievalPlan
from ms8.memory.retrieval.projection_sources import SearchProjectionCandidateProvider


def _state() -> ReplayState:
    claim = Claim(
        claim_id="claim:lexical",
        kind="fact",
        text=(
            "Use RetrievalPlan() with v0.2.18 at '/tmp/MS8 Data/config.json'.\n"
            "ms8 query --explain"
        ),
        subject="MS8",
        predicate="retrieval_command",
        value={
            "aliases": ["memory search", "hybrid retrieval"],
            "command": "ms8 query --explain",
        },
        scope="project:ms8",
        realm_id="realm:ms8",
        authority="user_explicit",
        sensitivity="internal",
        confidence=0.95,
        status="accepted",
        valid_time=ValidTime(start="2026-07-13T00:00:00Z", basis="user_explicit"),
        created_from_event_id="event:lexical",
    )
    evidence = Evidence(
        evidence_id="evidence:lexical",
        claim_id=claim.claim_id,
        event_id="event:lexical",
        relation="supports",
        fragment={
            "path": "/tmp/MS8 Data/config.json",
            "line": 7,
            "aliases": ["governed recall"],
        },
        quoted_text_hash="sha256:evidence",
    )
    return ReplayState(
        ledger_head="sha256:ledger",
        last_sequence=4,
        memory_events={},
        claims={
            claim.claim_id: ClaimReplayView(
                claim=claim,
                current_status="verified",
                decision_ids=(),
            )
        },
        evidence={evidence.evidence_id: evidence},
        decisions={},
        conflicts={},
        logical_state_hash="sha256:state",
    )


def _plan(text: str) -> RetrievalPlan:
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
            realm_ids=("realm:ms8",),
            scope="project:ms8",
        ),
        principal=principal,
        intent="code_symbol",
        realm_ids=("realm:ms8",),
    )


def test_search_projection_indexes_structured_and_exact_fields(tmp_path: Path) -> None:
    path = tmp_path / "search.json"
    result = SearchProjectionAdapter(path).rebuild_from_state(_state())
    payload = json.loads(path.read_text(encoding="utf-8"))
    document = payload["documents"][0]

    assert result.descriptor.schema == SEARCH_PROJECTION_SCHEMA
    assert result.descriptor.builder_version == SEARCH_BUILDER_VERSION == "3"
    assert document["subject"] == "MS8"
    assert document["predicate"] == "retrieval_command"
    assert "memory search" in document["aliases"]
    assert "governed recall" in document["aliases"]
    assert "RetrievalPlan()" in document["code_symbols"]
    assert "/tmp/MS8 Data/config.json" in document["paths"]
    assert "v0.2.18" in document["versions"]
    assert "--explain" in document["flags"]
    assert "ms8 query --explain" in document["commands"]
    assert document["evidence_ids"] == ["evidence:lexical"]
    assert document["evidence_text"]
    assert document["lifecycle"] == {
        "proposed_status": "accepted",
        "current_status": "verified",
        "latest_action": None,
    }
    assert document["valid_time"] == {
        "start": "2026-07-13T00:00:00Z",
        "end": None,
        "basis": "user_explicit",
    }


def test_exact_projection_terms_are_retrievable_inside_eligibility(tmp_path: Path) -> None:
    path = tmp_path / "search.json"
    SearchProjectionAdapter(path).rebuild_from_state(_state())
    provider = SearchProjectionCandidateProvider(
        path,
        lambda claim_id: ("evidence:lexical",) if claim_id == "claim:lexical" else (),
    )

    flag_hits = provider(_plan("--explain"), ("claim:lexical",), 5)
    path_hits = provider(_plan("'/tmp/MS8 Data/config.json'"), ("claim:lexical",), 5)

    assert [item.claim_id for item in flag_hits] == ["claim:lexical"]
    assert [item.claim_id for item in path_hits] == ["claim:lexical"]
    assert "--explain" in flag_hits[0].reason["matched_terms"]
    assert "/tmp/ms8 data/config.json" in path_hits[0].reason["matched_terms"]
