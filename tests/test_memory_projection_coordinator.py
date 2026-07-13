from __future__ import annotations

import json
from pathlib import Path

import pytest

from ms8.memory.application.projection_service import (
    ProjectionCoordinator,
    ProjectionNotReadyError,
)
from ms8.memory.domain.ledger import GENESIS_HASH, LedgerEvent, LedgerTransaction
from ms8.memory.domain.models import Actor, Claim, Decision, Evidence, MemoryEvent, ValidTime
from ms8.memory.infrastructure.graph_projection import GraphProjectionAdapter
from ms8.memory.infrastructure.jsonl_ledger import JsonlRecordStore
from ms8.memory.infrastructure.search_projection import SearchProjectionAdapter
from ms8.memory.infrastructure.sqlite_projection_adapter import SQLiteProjectionAdapter

FIXED_TIME = "2026-07-12T00:00:00+00:00"


def _initial_transaction() -> LedgerTransaction:
    event = MemoryEvent(
        event_id="evt_001",
        kind="document_fragment",
        content={"text": "MS8 supports Python and 本地记忆", "content_hash": "sha256:source"},
        source={"system": "absorb", "path_token": "docs/platform.md"},
        observed_at=FIXED_TIME,
        trust_class="untrusted_document",
    )
    claim = Claim(
        claim_id="clm_001",
        kind="fact",
        text="MS8 supports Python and 本地记忆",
        subject="project:ms8",
        predicate="supports",
        value=["python", "local-memory"],
        scope="project",
        realm_id="realm_ms8",
        authority="user_explicit",
        sensitivity="internal",
        confidence=0.98,
        status="proposed",
        valid_time=ValidTime(start="2026-07-01T00:00:00+00:00", basis="user_explicit"),
        created_from_event_id=event.event_id,
    )
    evidence = Evidence(
        evidence_id="evd_001",
        claim_id=claim.claim_id,
        event_id=event.event_id,
        relation="supports",
        fragment={"start_line": 1, "end_line": 1, "fragment_hash": "sha256:fragment"},
        quoted_text_hash="sha256:quote",
    )
    decision = Decision(
        decision_id="dec_001",
        action="admit",
        result_claim_id=claim.claim_id,
        result_status="accepted",
        policy={"engine_version": "policy-v1", "reason_codes": ["USER_EXPLICIT"]},
        actor=Actor(kind="user", id="sam"),
        reason="Accepted project capability",
        recorded_at=FIXED_TIME,
    )
    return LedgerTransaction.create(
        sequence=1,
        actor=Actor(kind="user", id="sam"),
        events=[
            LedgerEvent(type="memory_event.recorded", payload=event.to_dict()),
            LedgerEvent(type="claim.proposed", payload=claim.to_dict()),
            LedgerEvent(type="evidence.linked", payload=evidence.to_dict()),
            LedgerEvent(type="decision.made", payload=decision.to_dict()),
        ],
        prev_hash=GENESIS_HASH,
        transaction_id="txn_001",
        recorded_at=FIXED_TIME,
    )


def _coordinator(tmp_path: Path, store: JsonlRecordStore) -> tuple[ProjectionCoordinator, Path, Path, Path]:
    projection_root = tmp_path / "projections"
    sqlite_path = projection_root / "memory.sqlite"
    search_path = projection_root / "search.json"
    graph_path = projection_root / "graph.json"
    coordinator = ProjectionCoordinator(
        store,
        (
            SQLiteProjectionAdapter(sqlite_path),
            SearchProjectionAdapter(search_path),
            GraphProjectionAdapter(graph_path),
        ),
    )
    return coordinator, sqlite_path, search_path, graph_path


def test_projection_coordinator_builds_equivalent_sqlite_search_and_graph_artifacts(tmp_path: Path) -> None:
    store = JsonlRecordStore(tmp_path / "memory")
    transaction = _initial_transaction()
    store.append(transaction, expected_head=GENESIS_HASH)
    coordinator, sqlite_path, search_path, graph_path = _coordinator(tmp_path, store)

    build = coordinator.rebuild_all()
    status = coordinator.require_ready_for_query()

    assert build.ledger_head == transaction.hash
    assert len(build.projections) == 3
    assert {item.descriptor.name for item in build.projections} == {"sqlite", "search", "graph"}
    assert {item.descriptor.logical_state_hash for item in build.projections} == {build.logical_state_hash}
    assert status.ready_for_query is True
    assert sqlite_path.is_file()

    search = json.loads(search_path.read_text(encoding="utf-8"))
    assert search["manifest"]["document_count"] == 1
    assert search["postings"]["python"] == ["clm_001"]
    assert search["postings"]["本地"] == ["clm_001"]

    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    assert graph["manifest"]["node_count"] == 4
    assert any(
        edge["source"] == "evt_001"
        and edge["target"] == "clm_001"
        and edge["relation"] == "created_claim"
        for edge in graph["edges"]
    )
    assert any(
        edge["source"] == "dec_001"
        and edge["target"] == "clm_001"
        and edge["relation"] == "results_in_claim"
        for edge in graph["edges"]
    )


def test_projection_status_fails_closed_after_ledger_advances(tmp_path: Path) -> None:
    store = JsonlRecordStore(tmp_path / "memory")
    first = _initial_transaction()
    store.append(first, expected_head=GENESIS_HASH)
    coordinator, _sqlite_path, _search_path, _graph_path = _coordinator(tmp_path, store)
    coordinator.rebuild_all()

    event = MemoryEvent(
        event_id="evt_002",
        kind="system_observation",
        content={"text": "projection staleness test"},
        source={"system": "test-suite"},
        observed_at=FIXED_TIME,
        trust_class="system_observed",
    )
    second = LedgerTransaction.create(
        sequence=2,
        actor=Actor(kind="system", id="test-suite"),
        events=[LedgerEvent(type="memory_event.recorded", payload=event.to_dict())],
        prev_hash=first.hash,
        transaction_id="txn_002",
        recorded_at=FIXED_TIME,
    )
    store.append(second, expected_head=first.hash)

    status = coordinator.status()

    assert status.ready_for_query is False
    assert {item.reason for item in status.freshness} == {"projection_stale"}
    with pytest.raises(ProjectionNotReadyError):
        coordinator.require_ready_for_query()


def test_deleted_projection_is_detected_and_rebuild_is_logically_equivalent(tmp_path: Path) -> None:
    store = JsonlRecordStore(tmp_path / "memory")
    store.append(_initial_transaction(), expected_head=GENESIS_HASH)
    coordinator, _sqlite_path, search_path, _graph_path = _coordinator(tmp_path, store)
    first = coordinator.rebuild_all()
    search_path.unlink()

    missing = coordinator.status()

    assert missing.ready_for_query is False
    assert any(item.name == "search" and item.reason == "projection_missing_or_invalid" for item in missing.freshness)

    second = coordinator.rebuild_all()

    assert second.logical_state_hash == first.logical_state_hash
    assert coordinator.require_ready_for_query().ready_for_query is True


def test_tampered_json_projection_is_rejected_even_when_manifest_head_matches(tmp_path: Path) -> None:
    store = JsonlRecordStore(tmp_path / "memory")
    store.append(_initial_transaction(), expected_head=GENESIS_HASH)
    coordinator, _sqlite_path, search_path, graph_path = _coordinator(tmp_path, store)
    coordinator.rebuild_all()

    search = json.loads(search_path.read_text(encoding="utf-8"))
    search["documents"][0]["text"] = "tampered"
    search_path.write_text(json.dumps(search, ensure_ascii=False), encoding="utf-8")

    status = coordinator.status()

    assert status.ready_for_query is False
    assert any(item.name == "search" and item.reason == "projection_missing_or_invalid" for item in status.freshness)
    assert any(item.name == "graph" and item.reason == "ok" for item in status.freshness)
    assert graph_path.is_file()
