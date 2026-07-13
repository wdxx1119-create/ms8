from __future__ import annotations

from pathlib import Path

from ms8.memory.application.conflict_service import ConflictLedgerService
from ms8.memory.application.lifecycle import MemoryLifecycleService
from ms8.memory.application.replay import replay_transactions
from ms8.memory.domain.ledger import GENESIS_HASH, LedgerEvent, LedgerTransaction
from ms8.memory.domain.models import Actor, Claim, Decision, MemoryEvent, ValidTime
from ms8.memory.infrastructure.jsonl_ledger import JsonlRecordStore

T1 = "2026-07-12T01:00:00+00:00"
T2 = "2026-07-12T02:00:00+00:00"
T3 = "2026-07-12T03:00:00+00:00"


def _claim(claim_id: str, value: str) -> Claim:
    return Claim(
        claim_id=claim_id,
        kind="fact",
        text=f"release channel is {value}",
        subject="project:ms8",
        predicate="release_channel",
        value=value,
        scope="project",
        realm_id="realm_ms8",
        authority="user_explicit",
        sensitivity="internal",
        confidence=0.9,
        status="proposed",
        valid_time=ValidTime(start=T1, basis="user_explicit"),
        created_from_event_id="evt_release",
    )


def _admit(claim_id: str, decision_id: str) -> Decision:
    return Decision(
        decision_id=decision_id,
        action="admit",
        result_claim_id=claim_id,
        result_status="accepted",
        policy={"engine_version": "test"},
        actor=Actor(kind="user", id="sam"),
        reason="admit test claim",
        recorded_at=T1,
    )


def _initial_transaction() -> LedgerTransaction:
    event = MemoryEvent(
        event_id="evt_release",
        kind="user_input",
        content={"text": "release channel"},
        source={"system": "test-suite"},
        observed_at=T1,
        trust_class="user_explicit",
    )
    stable = _claim("clm_stable", "stable")
    alpha = _claim("clm_alpha", "alpha")
    return LedgerTransaction.create(
        sequence=1,
        prev_hash=GENESIS_HASH,
        actor=Actor(kind="user", id="sam"),
        transaction_id="txn_release",
        recorded_at=T1,
        events=(
            LedgerEvent(type="memory_event.recorded", payload=event.to_dict()),
            LedgerEvent(type="claim.proposed", payload=stable.to_dict()),
            LedgerEvent(type="decision.made", payload=_admit(stable.claim_id, "dec_stable").to_dict()),
            LedgerEvent(type="claim.proposed", payload=alpha.to_dict()),
            LedgerEvent(type="decision.made", payload=_admit(alpha.claim_id, "dec_alpha").to_dict()),
        ),
    )


def test_detected_conflict_is_recorded_once_then_resolved_by_decisions(tmp_path: Path) -> None:
    store = JsonlRecordStore(tmp_path / "memory")
    initial = _initial_transaction()
    store.append(initial, expected_head=GENESIS_HASH)
    conflicts = ConflictLedgerService(store)

    recorded = conflicts.record_detected(
        actor=Actor(kind="system", id="conflict-detector"),
        recorded_at=T2,
        expected_head_hash=initial.hash,
        transaction_id="txn_conflict_detected",
    )
    assert recorded.applied is True
    assert len(recorded.conflict_ids) == 1

    no_op = conflicts.record_detected(
        actor=Actor(kind="system", id="conflict-detector"),
        recorded_at=T3,
        expected_head_hash=recorded.new_head,
        transaction_id="txn_conflict_duplicate",
    )
    assert no_op.applied is False
    assert no_op.new_head == recorded.new_head

    state = replay_transactions(store.iterate())
    conflict_id = recorded.conflict_ids[0]
    assert tuple(state.conflicts[conflict_id]["claim_ids"]) == (
        "clm_alpha",
        "clm_stable",
    )

    resolved = MemoryLifecycleService(store).resolve_conflict(
        conflict_id=conflict_id,
        winning_claim_id="clm_stable",
        claim_ids=("clm_alpha", "clm_stable"),
        actor=Actor(kind="reviewer", id="reviewer-1"),
        reason="stable is the approved release channel",
        recorded_at=T3,
        expected_head_hash=recorded.new_head,
        decision_id="dec_resolve_release",
        transaction_id="txn_resolve_release",
    )
    final_state = replay_transactions(store.iterate())

    assert resolved.result_claim_id == "clm_stable"
    assert final_state.claims["clm_stable"].current_status == "accepted"
    assert final_state.claims["clm_alpha"].current_status == "revoked"
    assert conflict_id in final_state.conflicts
