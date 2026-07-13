from __future__ import annotations

from pathlib import Path

from ms8.memory.application.lifecycle import MemoryLifecycleService
from ms8.memory.application.temporal_query import query_claims
from ms8.memory.domain.ledger import GENESIS_HASH, LedgerEvent, LedgerTransaction
from ms8.memory.domain.models import Actor, Claim, Decision, MemoryEvent, ValidTime
from ms8.memory.infrastructure.jsonl_ledger import JsonlRecordStore

T1 = "2026-07-12T01:00:00+00:00"
T2 = "2026-07-12T02:00:00+00:00"


def _claim(claim_id: str, value: str, start: str) -> Claim:
    return Claim(
        claim_id=claim_id,
        kind="fact",
        text=f"deployment mode is {value}",
        subject="project:ms8",
        predicate="deployment_mode",
        value=value,
        scope="project",
        realm_id="realm_ms8",
        authority="user_explicit",
        sensitivity="internal",
        confidence=0.99,
        status="proposed",
        valid_time=ValidTime(start=start, basis="user_explicit"),
        created_from_event_id="evt_mode",
    )


def _initial_transaction() -> LedgerTransaction:
    event = MemoryEvent(
        event_id="evt_mode",
        kind="user_input",
        content={"text": "deployment mode"},
        source={"system": "test-suite"},
        observed_at=T1,
        trust_class="user_explicit",
    )
    claim = _claim("clm_local", "local", "2026-07-01T00:00:00+00:00")
    decision = Decision(
        decision_id="dec_local",
        action="admit",
        result_claim_id=claim.claim_id,
        result_status="accepted",
        policy={"engine_version": "test"},
        actor=Actor(kind="user", id="sam"),
        reason="initial deployment mode",
        recorded_at=T1,
    )
    return LedgerTransaction.create(
        sequence=1,
        prev_hash=GENESIS_HASH,
        actor=Actor(kind="user", id="sam"),
        transaction_id="txn_local",
        recorded_at=T1,
        events=(
            LedgerEvent(type="memory_event.recorded", payload=event.to_dict()),
            LedgerEvent(type="claim.proposed", payload=claim.to_dict()),
            LedgerEvent(type="decision.made", payload=decision.to_dict()),
        ),
    )


def test_supersede_derives_old_valid_time_end_from_replacement_start(tmp_path: Path) -> None:
    store = JsonlRecordStore(tmp_path / "memory")
    initial = _initial_transaction()
    store.append(initial, expected_head=GENESIS_HASH)
    replacement = _claim("clm_remote", "remote", T2)
    service = MemoryLifecycleService(store)

    service.supersede(
        target_claim_id="clm_local",
        replacement=replacement,
        actor=Actor(kind="user", id="sam"),
        reason="remote deployment became effective",
        recorded_at=T2,
        expected_head_hash=initial.hash,
        decision_id="dec_lan",
        transaction_id="txn_remote",
    )

    before = query_claims(
        store.iterate(),
        recorded_as_of=T2,
        valid_at="2026-07-12T01:59:59+00:00",
        include_inactive=True,
    )
    at_boundary = query_claims(
        store.iterate(),
        recorded_as_of=T2,
        valid_at=T2,
        include_inactive=True,
    )

    assert [(item.claim_id, item.current_status) for item in before] == [
        ("clm_local", "superseded")
    ]
    assert before[0].effective_valid_until == T2
    assert [(item.claim_id, item.current_status) for item in at_boundary] == [
        ("clm_remote", "accepted")
    ]
