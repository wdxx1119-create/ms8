from __future__ import annotations

from pathlib import Path

import pytest

from ms8.memory.application.legacy_migration import (
    LegacyMigrationError,
    LegacyMigrationStagingService,
    prepare_legacy_migration,
)
from ms8.memory.application.projection_service import ProjectionCoordinator
from ms8.memory.application.replay import replay_transactions
from ms8.memory.infrastructure.graph_projection import GraphProjectionAdapter
from ms8.memory.infrastructure.jsonl_ledger import JsonlRecordStore
from ms8.memory.infrastructure.search_projection import SearchProjectionAdapter
from ms8.memory.infrastructure.sqlite_projection_adapter import SQLiteProjectionAdapter

FIXED_TIME = "2026-07-12T04:00:00+00:00"


def _legacy_record(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": "legacy-001",
        "text": "The user prefers deterministic release checklists.",
        "normalized_text": "The user prefers deterministic release checklists.",
        "category": "user_preference",
        "status": "verified",
        "source": "ask",
        "created_at": "2026-06-01T08:00:00+00:00",
        "meta": {"admission": "ms8_write_guard_v1", "confidence": 0.91},
        "scope": "personal",
        "authority": "user_explicit",
        "sensitivity": "private",
        "can_recall": True,
        "can_inject": True,
        "can_act_on": False,
        "future_extension": {"origin": "legacy-plugin", "revision": 3},
    }
    row.update(overrides)
    return row


def _coordinator(root: Path, store: JsonlRecordStore) -> ProjectionCoordinator:
    projection_root = root / "projections"
    return ProjectionCoordinator(
        store,
        (
            SQLiteProjectionAdapter(projection_root / "memory.sqlite"),
            SearchProjectionAdapter(projection_root / "search.json"),
            GraphProjectionAdapter(projection_root / "graph.json"),
        ),
    )


def test_prepare_is_deterministic_and_does_not_write_files(tmp_path: Path) -> None:
    records = [_legacy_record()]

    first = prepare_legacy_migration(
        records,
        migration_id="migration_001",
        recorded_at=FIXED_TIME,
    )
    second = prepare_legacy_migration(
        records,
        migration_id="migration_001",
        recorded_at=FIXED_TIME,
    )

    assert first.plan == second.plan
    assert [item.hash for item in first.transactions] == [item.hash for item in second.transactions]
    assert first.plan.source_count == 1
    assert first.plan.migratable_count == 1
    assert first.plan.rejected_count == 0
    assert not any(tmp_path.iterdir())


def test_unknown_fields_are_preserved_in_legacy_meta() -> None:
    prepared = prepare_legacy_migration(
        [_legacy_record()],
        migration_id="migration_001",
        recorded_at=FIXED_TIME,
    )
    state = replay_transactions(prepared.transactions)
    preview = prepared.plan.previews[0]
    event = state.memory_events[preview.event_id]
    decision = state.decisions[preview.decision_id]

    assert preview.mapped_status == "verified"
    assert preview.preserved_unknown_fields == ("future_extension",)
    assert event.to_dict()["content"]["legacy_meta"]["future_extension"]["revision"] == 3
    assert decision.to_dict()["policy"]["legacy_meta"]["future_extension"]["origin"] == "legacy-plugin"
    assert state.claims[preview.claim_id].current_status == "verified"


def test_invalid_rows_and_unknown_statuses_are_reported_without_writes() -> None:
    prepared = prepare_legacy_migration(
        [
            "not-an-object",
            _legacy_record(id="legacy-empty", text="", normalized_text=""),
            _legacy_record(id="legacy-unknown", status="retired"),
        ],
        migration_id="migration_002",
        recorded_at=FIXED_TIME,
    )

    assert prepared.plan.source_count == 3
    assert prepared.plan.migratable_count == 1
    assert prepared.plan.rejected_count == 2
    assert {item.code for item in prepared.plan.issues} == {
        "invalid_record_type",
        "record_rejected",
    }
    assert prepared.plan.previews[0].mapped_status == "proposed"
    assert "unsupported legacy status" in prepared.plan.previews[0].warnings[0]


def test_staging_apply_builds_verified_ledger_and_all_projections(tmp_path: Path) -> None:
    prepared = prepare_legacy_migration(
        [
            _legacy_record(),
            _legacy_record(
                id="legacy-002",
                text="MS8 uses an append-only ledger.",
                normalized_text="MS8 uses an append-only ledger.",
                category="product_decision",
                status="accepted",
                source="system",
                authority="system_observed",
            ),
        ],
        migration_id="migration_003",
        recorded_at=FIXED_TIME,
    )
    store = JsonlRecordStore(tmp_path / "staging-memory")
    coordinator = _coordinator(tmp_path / "staging-memory", store)
    service = LegacyMigrationStagingService(store, coordinator)

    result = service.apply(prepared)

    assert result.applied_transactions == 2
    assert result.last_sequence == 2
    assert result.logical_state_hash is not None
    assert set(result.projection_names) == {"sqlite", "search", "graph"}
    assert store.verify().valid is True
    assert coordinator.require_ready_for_query().ready_for_query is True

    with pytest.raises(LegacyMigrationError, match="staging ledger must be empty"):
        service.apply(prepared)


def test_staging_apply_refuses_plan_with_rejected_records(tmp_path: Path) -> None:
    prepared = prepare_legacy_migration(
        [_legacy_record(), None],
        migration_id="migration_004",
        recorded_at=FIXED_TIME,
    )
    store = JsonlRecordStore(tmp_path / "staging-memory")

    with pytest.raises(LegacyMigrationError, match="contains rejected records"):
        LegacyMigrationStagingService(store).apply(prepared)

    assert store.verify().transaction_count == 0
