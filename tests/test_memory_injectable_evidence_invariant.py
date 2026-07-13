from __future__ import annotations

from pathlib import Path

import pytest

from ms8.memory.application.projection_service import ProjectionCoordinator
from ms8.memory.application.replay import ReplayIntegrityError, replay_transactions
from ms8.memory.domain.ledger import GENESIS_HASH, LedgerEvent, LedgerTransaction
from ms8.memory.domain.models import Actor, Claim, Decision, Evidence, MemoryEvent, ValidTime
from ms8.memory.infrastructure.jsonl_ledger import JsonlRecordStore
from ms8.memory.infrastructure.search_projection import SearchProjectionAdapter

RECORDED_AT = "2026-07-12T14:00:00+00:00"


def _event() -> MemoryEvent:
    return MemoryEvent(
        event_id="evt_injectable_001",
        kind="user_input",
        content={"text": "User prefers evidence-backed context"},
        source={"system": "test-suite", "document": "conversation.jsonl"},
        observed_at="2026-07-12T13:55:00+00:00",
        trust_class="user_explicit",
    )


def _claim() -> Claim:
    return Claim(
        claim_id="clm_injectable_001",
        kind="preference",
        text="User prefers evidence-backed context",
        subject="user:current",
        predicate="context_evidence",
        value=True,
        scope="user",
        realm_id="realm_personal",
        authority="user_explicit",
        sensitivity="internal",
        confidence=0.99,
        status="proposed",
        valid_time=ValidTime(start="2026-07-12T00:00:00+00:00", basis="user_explicit"),
        created_from_event_id="evt_injectable_001",
    )


def _decision() -> Decision:
    return Decision(
        decision_id="dec_injectable_001",
        action="admit",
        result_claim_id="clm_injectable_001",
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
        reason="Explicitly admitted for evidence invariant test",
        recorded_at=RECORDED_AT,
    )


def _evidence(*, locator: bool = True, valid_hash: bool = True) -> Evidence:
    fragment = (
        {"path": "conversation.jsonl", "start_offset": 0, "end_offset": 39}
        if locator
        else {"note": "no stable locator"}
    )
    return Evidence(
        evidence_id="evd_injectable_001",
        claim_id="clm_injectable_001",
        event_id="evt_injectable_001",
        relation="supports",
        fragment=fragment,
        quoted_text_hash=("sha256:" + "a" * 64) if valid_hash else "not-a-sha256-reference",
    )


def _transaction(*, include_evidence: bool, locator: bool = True, valid_hash: bool = True) -> LedgerTransaction:
    events = [
        LedgerEvent(type="memory_event.recorded", payload=_event().to_dict()),
        LedgerEvent(type="claim.proposed", payload=_claim().to_dict()),
    ]
    if include_evidence:
        events.append(
            LedgerEvent(
                type="evidence.linked",
                payload=_evidence(locator=locator, valid_hash=valid_hash).to_dict(),
            )
        )
    events.append(LedgerEvent(type="decision.made", payload=_decision().to_dict()))
    return LedgerTransaction.create(
        sequence=1,
        prev_hash=GENESIS_HASH,
        actor=Actor(kind="user", id="sam"),
        transaction_id="txn_injectable_001",
        recorded_at=RECORDED_AT,
        events=events,
    )


def test_injectable_claim_requires_at_least_one_evidence_record() -> None:
    with pytest.raises(ReplayIntegrityError, match="requires at least one evidence"):
        replay_transactions((_transaction(include_evidence=False),))


def test_injectable_claim_requires_hash_and_locator_fragment() -> None:
    with pytest.raises(ReplayIntegrityError, match="source, sha256 hash, and locator"):
        replay_transactions((_transaction(include_evidence=True, locator=False),))
    with pytest.raises(ReplayIntegrityError, match="source, sha256 hash, and locator"):
        replay_transactions((_transaction(include_evidence=True, valid_hash=False),))


def test_traceable_injectable_claim_replays_successfully() -> None:
    state = replay_transactions((_transaction(include_evidence=True),))

    view = state.claims["clm_injectable_001"]
    assert view.current_status == "accepted"
    assert view.decision_ids == ("dec_injectable_001",)
    assert state.evidence["evd_injectable_001"].quoted_text_hash.startswith("sha256:")
    assert state.memory_events["evt_injectable_001"].source["document"] == "conversation.jsonl"


def test_invalid_injectable_transaction_never_enters_projection(tmp_path: Path) -> None:
    store = JsonlRecordStore(tmp_path / "ledger-v1")
    store.append(_transaction(include_evidence=False), expected_head=GENESIS_HASH)
    projection = SearchProjectionAdapter(tmp_path / "projections" / "search.json")
    coordinator = ProjectionCoordinator(store, (projection,))

    status = coordinator.status()

    assert status.ledger_valid is False
    assert status.ready_for_query is False
    assert status.reason_codes[0] == "replay_invalid"
    assert projection.artifact_path.exists() is False
    with pytest.raises(ReplayIntegrityError, match="requires at least one evidence"):
        coordinator.rebuild_all()
