from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ms8.memory.application.replay import ReplayIntegrityError, replay_transactions
from ms8.memory.domain.ledger import GENESIS_HASH, LedgerEvent, LedgerTransaction
from ms8.memory.domain.models import Actor, Claim, Decision, Evidence, MemoryEvent, ValidTime
from ms8.memory.infrastructure.jsonl_ledger import JsonlRecordStore
from ms8.memory.infrastructure.sqlite_projection import SQLiteProjectionBuilder
from ms8.memory.infrastructure.sqlite_projection_adapter import SQLiteProjectionAdapter

FIXED_TIME = "2026-07-12T00:00:00+00:00"


def _domain_objects() -> tuple[MemoryEvent, Claim, Evidence, Decision]:
    event = MemoryEvent(
        event_id="evt_001",
        kind="user_input",
        content={"text": "Project supports Python 3.10 through 3.13"},
        source={"system": "cli", "workspace_realm_id": "realm_alpha"},
        observed_at=FIXED_TIME,
        trust_class="user_explicit",
    )
    claim = Claim(
        claim_id="clm_001",
        kind="fact",
        text="Project supports Python 3.10 through 3.13",
        subject="project:current",
        predicate="supports_python",
        value=["3.10", "3.11", "3.12", "3.13"],
        scope="project",
        realm_id="realm_alpha",
        authority="user_explicit",
        sensitivity="internal",
        confidence=0.98,
        status="proposed",
        valid_time=ValidTime(start="2026-07-01T00:00:00+00:00", basis="user_explicit"),
        created_from_event_id="evt_001",
    )
    evidence = Evidence(
        evidence_id="evd_001",
        claim_id=claim.claim_id,
        event_id=event.event_id,
        relation="supports",
        fragment={"start_offset": 0, "end_offset": 43, "fragment_hash": "sha256:fragment"},
        quoted_text_hash="sha256:quote",
    )
    decision = Decision(
        decision_id="dec_001",
        action="admit",
        result_claim_id=claim.claim_id,
        result_status="accepted",
        policy={"engine_version": "policy-v1", "reason_codes": ["USER_EXPLICIT"]},
        actor=Actor(kind="user", id="sam"),
        reason="User confirmed project compatibility",
        recorded_at=FIXED_TIME,
    )
    return event, claim, evidence, decision


def _transaction() -> LedgerTransaction:
    event, claim, evidence, decision = _domain_objects()
    return LedgerTransaction.create(
        sequence=1,
        prev_hash=GENESIS_HASH,
        actor=Actor(kind="user", id="sam"),
        transaction_id="txn_001",
        recorded_at=FIXED_TIME,
        events=[
            LedgerEvent(type="memory_event.recorded", payload=event.to_dict()),
            LedgerEvent(type="claim.proposed", payload=claim.to_dict()),
            LedgerEvent(type="evidence.linked", payload=evidence.to_dict()),
            LedgerEvent(type="decision.made", payload=decision.to_dict()),
        ],
    )


def test_replay_applies_lifecycle_only_through_decision() -> None:
    state = replay_transactions([_transaction()])

    assert state.claims["clm_001"].claim.status == "proposed"
    assert state.claims["clm_001"].current_status == "accepted"
    assert state.claims["clm_001"].decision_ids == ("dec_001",)
    assert state.evidence["evd_001"].claim_id == "clm_001"
    assert state.logical_state_hash.startswith("sha256:")


def test_replay_rejects_unknown_evidence_reference() -> None:
    event, claim, evidence, _decision = _domain_objects()
    payload = evidence.to_dict()
    payload["claim_id"] = "clm_missing"
    transaction = LedgerTransaction.create(
        sequence=1,
        actor=Actor(kind="system", id="test"),
        recorded_at=FIXED_TIME,
        events=[
            LedgerEvent(type="memory_event.recorded", payload=event.to_dict()),
            LedgerEvent(type="claim.proposed", payload=claim.to_dict()),
            LedgerEvent(type="evidence.linked", payload=payload),
        ],
    )

    with pytest.raises(ReplayIntegrityError, match="unknown claim"):
        replay_transactions([transaction])


def test_sqlite_projection_rebuild_is_logically_repeatable(tmp_path: Path) -> None:
    store = JsonlRecordStore(tmp_path / "memory")
    transaction = _transaction()
    store.append(transaction, expected_head=GENESIS_HASH)
    projection_path = tmp_path / "memory" / "projections" / "memory.sqlite"
    builder = SQLiteProjectionBuilder(store, projection_path)

    first = builder.rebuild()
    first_hash = first.manifest.logical_state_hash
    projection_path.unlink()
    second = builder.rebuild()

    assert second.manifest.logical_state_hash == first_hash
    assert second.manifest.built_from_ledger_head == transaction.hash
    assert builder.freshness().fresh is True
    with sqlite3.connect(projection_path) as connection:
        row = connection.execute(
            "SELECT proposed_status, current_status FROM claims WHERE claim_id = ?",
            ("clm_001",),
        ).fetchone()
        counts = connection.execute(
            "SELECT (SELECT COUNT(*) FROM memory_events), (SELECT COUNT(*) FROM evidence), "
            "(SELECT COUNT(*) FROM decisions)"
        ).fetchone()
    assert row == ("proposed", "accepted")
    assert counts == (1, 1, 1)


def test_projection_reports_stale_after_ledger_advances(tmp_path: Path) -> None:
    store = JsonlRecordStore(tmp_path / "memory")
    first = _transaction()
    store.append(first)
    builder = SQLiteProjectionBuilder(store, tmp_path / "memory" / "projections" / "memory.sqlite")
    builder.rebuild()
    extra_event = MemoryEvent(
        event_id="evt_002",
        kind="system_observation",
        content={"text": "A later observation"},
        source={"system": "doctor"},
        observed_at=FIXED_TIME,
        trust_class="system_observed",
    )
    second = LedgerTransaction.create(
        sequence=2,
        prev_hash=first.hash,
        actor=Actor(kind="system", id="doctor"),
        recorded_at=FIXED_TIME,
        events=[LedgerEvent(type="memory_event.recorded", payload=extra_event.to_dict())],
    )
    store.append(second, expected_head=first.hash)

    freshness = builder.freshness()

    assert freshness.fresh is False
    assert freshness.reason == "projection_stale"
    assert freshness.projection_head == first.hash
    assert freshness.ledger_head == second.hash


def test_sqlite_projection_adapter_rejects_row_tampering(tmp_path: Path) -> None:
    transaction = _transaction()
    state = replay_transactions([transaction])
    projection_path = tmp_path / "memory.sqlite"
    adapter = SQLiteProjectionAdapter(projection_path)
    adapter.rebuild_from_state(state)

    with sqlite3.connect(projection_path) as connection:
        connection.execute(
            "UPDATE claims SET current_status = ? WHERE claim_id = ?",
            ("revoked", "clm_001"),
        )
        connection.commit()

    assert adapter.read_descriptor() is None
    freshness = adapter.freshness(transaction.hash)
    assert freshness.fresh is False
    assert freshness.reason == "projection_missing_or_invalid"
