from __future__ import annotations

from pathlib import Path

import pytest

from ms8.memory.application.conflicts import detect_conflicts, recommend_conflict
from ms8.memory.application.lifecycle import LifecycleMutationError, MemoryLifecycleService
from ms8.memory.application.replay import replay_transactions
from ms8.memory.application.temporal_query import query_as_of, query_claims
from ms8.memory.domain.ledger import GENESIS_HASH, LedgerEvent, LedgerTransaction
from ms8.memory.domain.models import Actor, Claim, Decision, MemoryEvent, ValidTime
from ms8.memory.infrastructure.jsonl_ledger import JsonlRecordStore
from ms8.memory.ports.record_store import HeadMismatchError

T1 = "2026-07-12T01:00:00+00:00"
T2 = "2026-07-12T02:00:00+00:00"
T3 = "2026-07-12T03:00:00+00:00"


def _event(event_id: str = "evt_001") -> MemoryEvent:
    return MemoryEvent(
        event_id=event_id,
        kind="user_input",
        content={"text": "theme preference"},
        source={"system": "test-suite"},
        observed_at=T1,
        trust_class="user_explicit",
    )


def _claim(
    claim_id: str,
    value: str,
    *,
    event_id: str = "evt_001",
    authority: str = "user_explicit",
    confidence: float = 0.9,
    start: str | None = "2026-07-01T00:00:00+00:00",
    end: str | None = None,
) -> Claim:
    return Claim(
        claim_id=claim_id,
        kind="preference",
        text=f"Theme is {value}",
        subject="user:current",
        predicate="theme",
        value=value,
        scope="user",
        realm_id="realm_personal",
        authority=authority,
        sensitivity="internal",
        confidence=confidence,
        status="proposed",
        valid_time=ValidTime(start=start, end=end, basis="user_explicit"),
        created_from_event_id=event_id,
    )


def _admit(claim_id: str, decision_id: str, recorded_at: str = T1) -> Decision:
    return Decision(
        decision_id=decision_id,
        action="admit",
        result_claim_id=claim_id,
        result_status="accepted",
        policy={"engine_version": "test"},
        actor=Actor(kind="user", id="sam"),
        reason="accepted for test",
        recorded_at=recorded_at,
    )


def _initial_transaction() -> LedgerTransaction:
    event = _event()
    claim = _claim("clm_old", "dark")
    decision = _admit(claim.claim_id, "dec_admit_old")
    return LedgerTransaction.create(
        sequence=1,
        prev_hash=GENESIS_HASH,
        actor=Actor(kind="user", id="sam"),
        transaction_id="txn_initial",
        recorded_at=T1,
        events=(
            LedgerEvent(type="memory_event.recorded", payload=event.to_dict()),
            LedgerEvent(type="claim.proposed", payload=claim.to_dict()),
            LedgerEvent(type="decision.made", payload=decision.to_dict()),
        ),
    )


def test_correct_appends_replacement_without_mutating_original(tmp_path: Path) -> None:
    store = JsonlRecordStore(tmp_path / "memory")
    initial = _initial_transaction()
    store.append(initial, expected_head=GENESIS_HASH)
    service = MemoryLifecycleService(store)
    replacement = _claim("clm_new", "light")

    result = service.correct(
        target_claim_id="clm_old",
        replacement=replacement,
        actor=Actor(kind="user", id="sam"),
        reason="corrected preference",
        recorded_at=T2,
        expected_head_hash=initial.hash,
        decision_id="dec_correct",
        transaction_id="txn_correct",
    )

    state = replay_transactions(store.iterate())
    assert result.previous_head == initial.hash
    assert state.claims["clm_old"].claim.value == "dark"
    assert state.claims["clm_old"].current_status == "superseded"
    assert state.claims["clm_new"].claim.value == "light"
    assert state.claims["clm_new"].current_status == "accepted"
    assert state.claims["clm_old"].claim is not state.claims["clm_new"].claim


def test_lifecycle_mutation_requires_current_expected_head(tmp_path: Path) -> None:
    store = JsonlRecordStore(tmp_path / "memory")
    initial = _initial_transaction()
    store.append(initial, expected_head=GENESIS_HASH)
    service = MemoryLifecycleService(store)

    with pytest.raises(LifecycleMutationError, match="expected_head_hash"):
        service.revoke(
            target_claim_id="clm_old",
            actor=Actor(kind="user", id="sam"),
            reason="missing token",
            recorded_at=T2,
            expected_head_hash="",
        )
    with pytest.raises(HeadMismatchError):
        service.revoke(
            target_claim_id="clm_old",
            actor=Actor(kind="user", id="sam"),
            reason="stale token",
            recorded_at=T2,
            expected_head_hash=GENESIS_HASH,
        )


def test_forget_hides_content_and_can_return_minimal_tombstone(tmp_path: Path) -> None:
    store = JsonlRecordStore(tmp_path / "memory")
    initial = _initial_transaction()
    store.append(initial, expected_head=GENESIS_HASH)
    service = MemoryLifecycleService(store)

    service.forget(
        target_claim_id="clm_old",
        actor=Actor(kind="user", id="sam"),
        reason="user requested logical forgetting",
        recorded_at=T2,
        expected_head_hash=initial.hash,
        decision_id="dec_forget",
        transaction_id="txn_forget",
    )

    assert query_claims(store.iterate()) == ()
    tombstones = query_claims(
        store.iterate(),
        include_forgotten_tombstones=True,
    )
    assert len(tombstones) == 1
    assert tombstones[0].claim is None
    assert tombstones[0].forgotten is True
    assert tombstones[0].tombstone == {
        "claim_id": "clm_old",
        "realm_id": "realm_personal",
        "current_status": "revoked",
        "decision_id": "dec_forget",
        "action": "forget",
    }


def test_as_of_query_separates_recorded_time_from_current_state(tmp_path: Path) -> None:
    store = JsonlRecordStore(tmp_path / "memory")
    initial = _initial_transaction()
    store.append(initial, expected_head=GENESIS_HASH)
    service = MemoryLifecycleService(store)
    service.correct(
        target_claim_id="clm_old",
        replacement=_claim("clm_new", "light"),
        actor=Actor(kind="user", id="sam"),
        reason="corrected preference",
        recorded_at=T2,
        expected_head_hash=initial.hash,
        decision_id="dec_correct",
        transaction_id="txn_correct",
    )

    before = query_as_of(store.iterate(), as_of="2026-07-12T01:30:00+00:00")
    after = query_as_of(store.iterate(), as_of=T3)

    assert [(item.claim_id, item.current_status) for item in before] == [
        ("clm_old", "accepted")
    ]
    assert [(item.claim_id, item.current_status) for item in after] == [
        ("clm_new", "accepted")
    ]


def test_valid_time_filter_uses_half_open_interval(tmp_path: Path) -> None:
    event = _event()
    claim = _claim(
        "clm_window",
        "dark",
        start="2026-07-10T00:00:00+00:00",
        end="2026-07-12T00:00:00+00:00",
    )
    transaction = LedgerTransaction.create(
        sequence=1,
        prev_hash=GENESIS_HASH,
        actor=Actor(kind="user", id="sam"),
        transaction_id="txn_window",
        recorded_at=T1,
        events=(
            LedgerEvent(type="memory_event.recorded", payload=event.to_dict()),
            LedgerEvent(type="claim.proposed", payload=claim.to_dict()),
            LedgerEvent(type="decision.made", payload=_admit(claim.claim_id, "dec_window").to_dict()),
        ),
    )

    assert len(query_claims((transaction,), valid_at="2026-07-11T23:59:59+00:00")) == 1
    assert query_claims((transaction,), valid_at="2026-07-12T00:00:00+00:00") == ()


def _conflicting_transaction() -> LedgerTransaction:
    event = _event()
    dark = _claim("clm_dark", "dark", authority="user_implicit", confidence=0.7)
    light = _claim("clm_light", "light", authority="user_explicit", confidence=0.95)
    return LedgerTransaction.create(
        sequence=1,
        prev_hash=GENESIS_HASH,
        actor=Actor(kind="user", id="sam"),
        transaction_id="txn_conflict",
        recorded_at=T1,
        events=(
            LedgerEvent(type="memory_event.recorded", payload=event.to_dict()),
            LedgerEvent(type="claim.proposed", payload=dark.to_dict()),
            LedgerEvent(type="decision.made", payload=_admit(dark.claim_id, "dec_dark").to_dict()),
            LedgerEvent(type="claim.proposed", payload=light.to_dict()),
            LedgerEvent(type="decision.made", payload=_admit(light.claim_id, "dec_light").to_dict()),
        ),
    )


def test_conflict_detection_retains_alternatives_and_explains_recommendation() -> None:
    state = replay_transactions((_conflicting_transaction(),))

    conflicts = detect_conflicts(state)
    recommendation = recommend_conflict(state, conflicts[0])

    assert len(conflicts) == 1
    assert conflicts[0].claim_ids == ("clm_dark", "clm_light")
    assert recommendation.recommended_claim_id == "clm_light"
    assert {item.claim_id for item in recommendation.alternatives} == {
        "clm_dark",
        "clm_light",
    }
    assert recommendation.explanation[-1] == "all alternatives remain retained and auditable"


def test_non_overlapping_values_do_not_conflict() -> None:
    event = _event()
    old = _claim(
        "clm_old_window",
        "dark",
        start="2026-01-01T00:00:00+00:00",
        end="2026-02-01T00:00:00+00:00",
    )
    new = _claim(
        "clm_new_window",
        "light",
        start="2026-02-01T00:00:00+00:00",
    )
    transaction = LedgerTransaction.create(
        sequence=1,
        prev_hash=GENESIS_HASH,
        actor=Actor(kind="user", id="sam"),
        transaction_id="txn_no_conflict",
        recorded_at=T1,
        events=(
            LedgerEvent(type="memory_event.recorded", payload=event.to_dict()),
            LedgerEvent(type="claim.proposed", payload=old.to_dict()),
            LedgerEvent(type="claim.proposed", payload=new.to_dict()),
        ),
    )

    assert detect_conflicts(replay_transactions((transaction,))) == ()


def test_resolve_conflict_keeps_losing_claim_auditable(tmp_path: Path) -> None:
    store = JsonlRecordStore(tmp_path / "memory")
    initial = _conflicting_transaction()
    store.append(initial, expected_head=GENESIS_HASH)
    state = replay_transactions(store.iterate())
    conflict = detect_conflicts(state)[0]
    service = MemoryLifecycleService(store)

    result = service.resolve_conflict(
        conflict_id=conflict.conflict_id,
        winning_claim_id="clm_light",
        claim_ids=conflict.claim_ids,
        actor=Actor(kind="reviewer", id="reviewer-1"),
        reason="explicit user preference has higher authority",
        recorded_at=T2,
        expected_head_hash=initial.hash,
        decision_id="dec_resolve",
        transaction_id="txn_resolve",
    )

    resolved = replay_transactions(store.iterate())
    assert result.result_claim_id == "clm_light"
    assert resolved.claims["clm_light"].current_status == "accepted"
    assert resolved.claims["clm_dark"].current_status == "revoked"
    assert resolved.claims["clm_dark"].claim.value == "dark"
    assert "dec_resolve" in resolved.decisions
