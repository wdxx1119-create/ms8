from __future__ import annotations

from pathlib import Path

import pytest

from ms8.memory.application.projection_service import ProjectionCoordinator
from ms8.memory.application.replay import ReplayIntegrityError
from ms8.memory.domain.ledger import GENESIS_HASH, LedgerEvent, LedgerTransaction
from ms8.memory.domain.models import Actor, Claim, ValidTime
from ms8.memory.infrastructure.jsonl_ledger import JsonlRecordStore
from ms8.memory.infrastructure.search_projection import SearchProjectionAdapter

FIXED_TIME = "2026-07-12T00:00:00+00:00"


def test_projection_status_fails_closed_when_ledger_is_structurally_valid_but_semantically_invalid(
    tmp_path: Path,
) -> None:
    store = JsonlRecordStore(tmp_path / "memory")
    claim = Claim(
        claim_id="clm_missing_source",
        kind="fact",
        text="This claim references a missing event",
        subject="project:ms8",
        predicate="invalid_test",
        value=True,
        scope="project",
        realm_id="realm_ms8",
        authority="user_explicit",
        sensitivity="internal",
        confidence=1.0,
        status="proposed",
        valid_time=ValidTime(basis="unknown"),
        created_from_event_id="evt_missing",
    )
    transaction = LedgerTransaction.create(
        sequence=1,
        actor=Actor(kind="system", id="test-suite"),
        events=[LedgerEvent(type="claim.proposed", payload=claim.to_dict())],
        prev_hash=GENESIS_HASH,
        transaction_id="txn_semantic_error",
        recorded_at=FIXED_TIME,
    )
    store.append(transaction, expected_head=GENESIS_HASH)
    coordinator = ProjectionCoordinator(
        store,
        (SearchProjectionAdapter(tmp_path / "projections" / "search.json"),),
    )

    status = coordinator.status()

    assert status.ledger_valid is False
    assert status.ready_for_query is False
    assert status.reason_codes[0] == "replay_invalid"
    with pytest.raises(ReplayIntegrityError):
        coordinator.rebuild_all()
