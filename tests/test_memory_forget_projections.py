from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ms8.memory.application.lifecycle import MemoryLifecycleService
from ms8.memory.application.projection_service import ProjectionCoordinator
from ms8.memory.domain.ledger import GENESIS_HASH, LedgerEvent, LedgerTransaction
from ms8.memory.domain.models import Actor, Claim, Decision, MemoryEvent, ValidTime
from ms8.memory.infrastructure.graph_projection import GraphProjectionAdapter
from ms8.memory.infrastructure.jsonl_ledger import JsonlRecordStore
from ms8.memory.infrastructure.search_projection import SearchProjectionAdapter
from ms8.memory.infrastructure.sqlite_projection_adapter import SQLiteProjectionAdapter

T1 = "2026-07-12T01:00:00+00:00"
T2 = "2026-07-12T02:00:00+00:00"


def _initial_transaction() -> LedgerTransaction:
    event = MemoryEvent(
        event_id="evt_secret",
        kind="user_input",
        content={"text": "private preference is dark"},
        source={"system": "test-suite"},
        observed_at=T1,
        trust_class="user_explicit",
    )
    claim = Claim(
        claim_id="clm_secret",
        kind="preference",
        text="private preference is dark",
        subject="user:current",
        predicate="private_theme",
        value="dark",
        scope="user",
        realm_id="realm_private",
        authority="user_explicit",
        sensitivity="private",
        confidence=1.0,
        status="proposed",
        valid_time=ValidTime(start=T1, basis="user_explicit"),
        created_from_event_id=event.event_id,
    )
    decision = Decision(
        decision_id="dec_admit_secret",
        action="admit",
        result_claim_id=claim.claim_id,
        result_status="accepted",
        policy={"engine_version": "test"},
        actor=Actor(kind="user", id="sam"),
        reason="accept private preference",
        recorded_at=T1,
    )
    return LedgerTransaction.create(
        sequence=1,
        prev_hash=GENESIS_HASH,
        actor=Actor(kind="user", id="sam"),
        transaction_id="txn_secret",
        recorded_at=T1,
        events=(
            LedgerEvent(type="memory_event.recorded", payload=event.to_dict()),
            LedgerEvent(type="claim.proposed", payload=claim.to_dict()),
            LedgerEvent(type="decision.made", payload=decision.to_dict()),
        ),
    )


def test_forget_is_suppressed_from_recallable_sqlite_search_and_graph(tmp_path: Path) -> None:
    store = JsonlRecordStore(tmp_path / "memory")
    initial = _initial_transaction()
    store.append(initial, expected_head=GENESIS_HASH)
    MemoryLifecycleService(store).forget(
        target_claim_id="clm_secret",
        actor=Actor(kind="user", id="sam"),
        reason="forget private preference",
        recorded_at=T2,
        expected_head_hash=initial.hash,
        decision_id="dec_forget_secret",
        transaction_id="txn_forget_secret",
    )

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
    build = coordinator.rebuild_all()

    assert coordinator.require_ready_for_query().ready_for_query is True
    assert len(build.projections) == 3

    search = json.loads(search_path.read_text(encoding="utf-8"))
    assert search["manifest"]["document_count"] == 0
    assert search["postings"] == {}
    assert "private preference is dark" not in search_path.read_text(encoding="utf-8")

    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    claim_nodes = [node for node in graph["nodes"] if node["id"] == "clm_secret"]
    assert claim_nodes == [
        {
            "id": "clm_secret",
            "type": "claim_tombstone",
            "attributes": {
                "realm_id": "realm_private",
                "current_status": "revoked",
                "decision_id": "dec_forget_secret",
            },
        }
    ]
    assert "private preference is dark" not in graph_path.read_text(encoding="utf-8")

    with sqlite3.connect(sqlite_path) as connection:
        recallable_claims = connection.execute(
            "SELECT COUNT(*) FROM recallable_claims"
        ).fetchone()
        recallable_events = connection.execute(
            "SELECT COUNT(*) FROM recallable_memory_events"
        ).fetchone()
        tombstone = connection.execute(
            "SELECT claim_id, realm_id, decision_id, action FROM claim_tombstones"
        ).fetchone()
        base_row = connection.execute(
            "SELECT is_forgotten, current_status FROM claims WHERE claim_id = ?",
            ("clm_secret",),
        ).fetchone()

    assert recallable_claims == (0,)
    assert recallable_events == (0,)
    assert tombstone == (
        "clm_secret",
        "realm_private",
        "dec_forget_secret",
        "forget",
    )
    assert base_row == (1, "revoked")
