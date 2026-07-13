from __future__ import annotations

from pathlib import Path

import pytest

from ms8.memory.application.legacy_migration import LegacyMigrationStagingService, prepare_legacy_migration
from ms8.memory.application.projection_recovery import ProjectionRecoveryError, ProjectionRecoveryService
from ms8.memory.application.projection_service import ProjectionCoordinator
from ms8.memory.application.retrieval_context import RetrievalEngine, RetrievalRequest
from ms8.memory.infrastructure.graph_projection import GraphProjectionAdapter
from ms8.memory.infrastructure.jsonl_ledger import JsonlRecordStore
from ms8.memory.infrastructure.search_projection import SearchProjectionAdapter
from ms8.memory.infrastructure.sqlite_projection_adapter import SQLiteProjectionAdapter
from ms8.memory.ports.record_store import HeadMismatchError, LedgerIntegrityError

RECORDED_AT = "2026-07-12T12:00:00+00:00"


def _rows() -> list[dict[str, object]]:
    return [
        {
            "id": "legacy-recovery-1",
            "text": "User prefers deterministic recovery reports",
            "normalized_text": "User prefers deterministic recovery reports",
            "category": "user_preference",
            "status": "verified",
            "source": "ask",
            "created_at": "2026-07-01T01:02:03+00:00",
            "meta": {"confidence": 0.95, "workspace_realm_id": "project:ms8"},
            "scope": "project",
            "authority": "user_explicit",
            "sensitivity": "private",
            "can_recall": True,
            "can_inject": True,
            "can_act_on": False,
        },
        {
            "id": "legacy-recovery-2",
            "text": "Projection rebuilds must preserve ledger semantics",
            "normalized_text": "Projection rebuilds must preserve ledger semantics",
            "category": "product_decision",
            "status": "accepted",
            "source": "system",
            "created_at": "2026-07-02T04:05:06+00:00",
            "meta": {"confidence": 0.9, "workspace_realm_id": "project:ms8"},
            "scope": "project",
            "authority": "system_observed",
            "sensitivity": "private",
            "can_recall": True,
            "can_inject": False,
            "can_act_on": False,
        },
    ]


def _runtime(
    tmp_path: Path,
) -> tuple[
    JsonlRecordStore,
    ProjectionCoordinator,
    ProjectionRecoveryService,
    RetrievalEngine,
    tuple[Path, Path, Path],
]:
    prepared = prepare_legacy_migration(
        _rows(),
        migration_id="mig_projection_recovery_001",
        recorded_at=RECORDED_AT,
    )
    store = JsonlRecordStore(tmp_path / "ledger-v1")
    LegacyMigrationStagingService(store).apply(prepared)

    sqlite_path = tmp_path / "projections" / "memory.sqlite3"
    search_path = tmp_path / "projections" / "search.json"
    graph_path = tmp_path / "projections" / "graph.json"
    coordinator = ProjectionCoordinator(
        store,
        (
            SQLiteProjectionAdapter(sqlite_path),
            SearchProjectionAdapter(search_path),
            GraphProjectionAdapter(graph_path),
        ),
    )
    coordinator.rebuild_all()
    service = ProjectionRecoveryService(coordinator)
    engine = RetrievalEngine(
        record_store=store,
        projection_coordinator=coordinator,
        search_projection_path=search_path,
    )
    return store, coordinator, service, engine, (sqlite_path, search_path, graph_path)


def _artifact_bytes(paths: tuple[Path, Path, Path]) -> tuple[bytes, bytes, bytes]:
    return tuple(path.read_bytes() for path in paths)  # type: ignore[return-value]


def test_preview_is_read_only_and_reports_current_projection_state(tmp_path: Path) -> None:
    store, _, service, _, paths = _runtime(tmp_path)
    before = _artifact_bytes(paths)

    preview = service.preview()

    verification = store.verify()
    assert preview.ledger_head == verification.last_valid_hash
    assert preview.last_sequence == verification.last_sequence
    assert preview.ready_before is True
    assert preview.reason_codes == ()
    assert {item["name"] for item in preview.projection_states} == {"sqlite", "search", "graph"}
    assert preview.required_confirmation == f"REBUILD_PROJECTIONS:{preview.ledger_head}"
    assert _artifact_bytes(paths) == before


def test_dry_run_does_not_recreate_missing_projections_then_apply_recovers(tmp_path: Path) -> None:
    store, coordinator, service, engine, paths = _runtime(tmp_path)
    expected_head = str(store.verify().last_valid_hash)
    for path in paths:
        path.unlink()

    assert coordinator.status().ready_for_query is False
    dry_run = service.rebuild(expected_head)
    assert dry_run.applied is False
    assert dry_run.ready_before is False
    assert dry_run.ready_after is False
    assert not any(path.exists() for path in paths)

    applied = service.rebuild(
        expected_head,
        apply=True,
        confirmation=service.confirmation_token(expected_head),
    )
    assert applied.applied is True
    assert applied.ready_before is False
    assert applied.ready_after is True
    assert applied.rebuilt_projections == ("sqlite", "search", "graph")
    assert all(path.exists() for path in paths)
    assert coordinator.status().ready_for_query is True
    assert engine.retrieve(RetrievalRequest(text="deterministic recovery")).hits


def test_tampered_projection_fails_closed_and_rebuild_restores_query_semantics(tmp_path: Path) -> None:
    store, coordinator, service, engine, paths = _runtime(tmp_path)
    expected_head = str(store.verify().last_valid_hash)
    before = engine.retrieve(RetrievalRequest(text="projection ledger", limit=10)).to_dict()

    paths[1].write_text("{}\n", encoding="utf-8")
    status = coordinator.status()
    assert status.ready_for_query is False
    assert any(reason.startswith("search:") for reason in status.reason_codes)

    result = service.rebuild(
        expected_head,
        apply=True,
        confirmation=service.confirmation_token(expected_head),
    )
    after = engine.retrieve(RetrievalRequest(text="projection ledger", limit=10)).to_dict()

    assert result.ready_after is True
    assert before == after


def test_expected_head_and_exact_confirmation_are_required(tmp_path: Path) -> None:
    store, _, service, _, paths = _runtime(tmp_path)
    expected_head = str(store.verify().last_valid_hash)
    before = _artifact_bytes(paths)

    with pytest.raises(HeadMismatchError):
        service.rebuild("sha256:" + "f" * 64, apply=True, confirmation="irrelevant")
    with pytest.raises(ProjectionRecoveryError, match="exact rebuild confirmation"):
        service.rebuild(expected_head, apply=True, confirmation="REBUILD_PROJECTIONS")

    assert _artifact_bytes(paths) == before


def test_invalid_ledger_refuses_rebuild_without_touching_projections(tmp_path: Path) -> None:
    store, _, service, _, paths = _runtime(tmp_path)
    expected_head = str(store.verify().last_valid_hash)
    before = _artifact_bytes(paths)
    with store.ledger_path.open("ab") as handle:
        handle.write(b"not-a-ledger-transaction\n")

    with pytest.raises(LedgerIntegrityError, match="invalid ledger"):
        service.rebuild(
            expected_head,
            apply=True,
            confirmation=service.confirmation_token(expected_head),
        )

    assert _artifact_bytes(paths) == before


def test_repeated_rebuild_is_logically_idempotent(tmp_path: Path) -> None:
    store, coordinator, service, engine, _ = _runtime(tmp_path)
    expected_head = str(store.verify().last_valid_hash)
    confirmation = service.confirmation_token(expected_head)
    before_query = engine.retrieve(RetrievalRequest(text="rebuild recovery", limit=10)).to_dict()

    first = service.rebuild(expected_head, apply=True, confirmation=confirmation)
    second = service.rebuild(expected_head, apply=True, confirmation=confirmation)
    after_query = engine.retrieve(RetrievalRequest(text="rebuild recovery", limit=10)).to_dict()

    assert first.logical_state_hash == second.logical_state_hash
    assert first.ledger_head == second.ledger_head == expected_head
    assert first.last_sequence == second.last_sequence
    assert coordinator.status().ready_for_query is True
    assert before_query == after_query
