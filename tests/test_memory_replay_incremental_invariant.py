from __future__ import annotations

import pytest

from ms8.memory.application.replay import ReplayIntegrityError, replay_transactions
from ms8.memory.domain.ledger import GENESIS_HASH, LedgerEvent, LedgerTransaction
from ms8.memory.domain.models import Actor, Claim, Decision, Evidence, MemoryEvent, ValidTime

T1 = "2026-07-12T12:00:00+00:00"
T2 = "2026-07-12T12:01:00+00:00"


def _event() -> MemoryEvent:
    return MemoryEvent(
        event_id="evt_incremental",
        kind="user_input",
        content={"text": "Incremental evidence invariant"},
        source={"system": "test", "path": "conversation.jsonl"},
        observed_at=T1,
        trust_class="user_explicit",
    )


def _claim() -> Claim:
    return Claim(
        claim_id="clm_incremental",
        kind="fact",
        text="Incremental evidence invariant",
        subject="project:ms8",
        predicate="evidence_invariant",
        value=True,
        scope="project",
        realm_id="realm_ms8",
        authority="user_explicit",
        sensitivity="internal",
        confidence=1.0,
        status="proposed",
        valid_time=ValidTime(start=T1, basis="user_explicit"),
        created_from_event_id="evt_incremental",
    )


def _injectable_decision() -> Decision:
    return Decision(
        decision_id="dec_incremental",
        action="admit",
        result_claim_id="clm_incremental",
        result_status="accepted",
        policy={
            "engine_version": "test-policy-v1",
            "governance": {
                "can_recall": True,
                "can_inject": True,
                "can_act_on": False,
            },
        },
        actor=Actor(kind="user", id="sam"),
        reason="accept invariant",
        recorded_at=T2,
    )


def _evidence() -> Evidence:
    return Evidence(
        evidence_id="evd_incremental",
        claim_id="clm_incremental",
        event_id="evt_incremental",
        relation="supports",
        fragment={"path": "conversation.jsonl", "start_offset": 0, "end_offset": 30},
        quoted_text_hash="sha256:" + "a" * 64,
    )


def test_later_evidence_cannot_repair_an_earlier_invalid_injectable_transaction() -> None:
    first = LedgerTransaction.create(
        sequence=1,
        prev_hash=GENESIS_HASH,
        transaction_id="txn_invalid_injectable",
        actor=Actor(kind="user", id="sam"),
        recorded_at=T1,
        events=(
            LedgerEvent(type="memory_event.recorded", payload=_event().to_dict()),
            LedgerEvent(type="claim.proposed", payload=_claim().to_dict()),
            LedgerEvent(type="decision.made", payload=_injectable_decision().to_dict()),
        ),
    )
    second = LedgerTransaction.create(
        sequence=2,
        prev_hash=first.hash,
        transaction_id="txn_late_evidence",
        actor=Actor(kind="user", id="sam"),
        recorded_at=T2,
        events=(LedgerEvent(type="evidence.linked", payload=_evidence().to_dict()),),
    )

    with pytest.raises(ReplayIntegrityError, match="requires at least one evidence"):
        replay_transactions((first, second))


def test_evidence_and_injection_decision_can_activate_a_previously_noninjectable_claim() -> None:
    first = LedgerTransaction.create(
        sequence=1,
        prev_hash=GENESIS_HASH,
        transaction_id="txn_proposed_noninjectable",
        actor=Actor(kind="user", id="sam"),
        recorded_at=T1,
        events=(
            LedgerEvent(type="memory_event.recorded", payload=_event().to_dict()),
            LedgerEvent(type="claim.proposed", payload=_claim().to_dict()),
        ),
    )
    second = LedgerTransaction.create(
        sequence=2,
        prev_hash=first.hash,
        transaction_id="txn_evidence_and_admit",
        actor=Actor(kind="user", id="sam"),
        recorded_at=T2,
        events=(
            LedgerEvent(type="evidence.linked", payload=_evidence().to_dict()),
            LedgerEvent(type="decision.made", payload=_injectable_decision().to_dict()),
        ),
    )

    state = replay_transactions((first, second))

    assert state.claims["clm_incremental"].current_status == "accepted"
    assert state.evidence["evd_incremental"].claim_id == "clm_incremental"
