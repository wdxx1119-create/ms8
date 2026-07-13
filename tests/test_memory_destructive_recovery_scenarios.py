from __future__ import annotations

from pathlib import Path

import pytest

from ms8.memory.application.legacy_migration import LegacyMigrationStagingService, prepare_legacy_migration
from ms8.memory.application.projection_recovery import ProjectionRecoveryService
from ms8.memory.application.projection_service import ProjectionCoordinator, ProjectionNotReadyError
from ms8.memory.application.replay import ReplayState
from ms8.memory.application.retrieval_context import RetrievalEngine, RetrievalRequest
from ms8.memory.domain.ledger import GENESIS_HASH, LedgerEvent, LedgerTransaction
from ms8.memory.domain.models import Actor
from ms8.memory.infrastructure.graph_projection import GraphProjectionAdapter
from ms8.memory.infrastructure.jsonl_ledger import JsonlRecordStore
from ms8.memory.infrastructure.search_projection import SearchProjectionAdapter
from ms8.memory.infrastructure.sqlite_projection_adapter import SQLiteProjectionAdapter
from ms8.memory.ports.projection import ProjectionBuildResult
from ms8.memory.ports.record_store import HeadMismatchError, LedgerIntegrityError

RECORDED_AT = "2026-07-12T13:00:00+00:00"
CONFLICT_AT = "2026-07-12T13:10:00+00:00"


def _rows() -> list[dict[str, object]]:
    return [
        {
            "id": "legacy-destructive-1",
            "text": "User expects destructive recovery drills",
            "normalized_text": "User expects destructive recovery drills",
            "category": "user_preference",
            "status": "verified",
            "source": "ask",
            "created_at": "2026-07-01T01:02:03+00:00",
            "meta": {"confidence": 0.96, "workspace_realm_id": "project:ms8"},
            "scope": "project",
            "authority": "user_explicit",
            "sensitivity": "private",
            "can_recall": True,
            "can_inject": True,
            "can_act_on": False,
        },
        {
            "id": "legacy-destructive-2",
            "text": "Projection failures must remain recoverable",
            "normalized_text": "Projection failures must remain recoverable",
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
    tuple[str, str],
]:
    prepared = prepare_legacy_migration(
        _rows(),
        migration_id="mig_destructive_recovery_001",
        recorded_at=RECORDED_AT,
    )
    store = JsonlRecordStore(tmp_path / "ledger-v1")
    LegacyMigrationStagingService(store).apply(prepared)
    claim_ids = tuple(item.claim_id for item in prepared.plan.previews)
    assert len(claim_ids) == 2

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
    recovery = ProjectionRecoveryService(coordinator)
    engine = RetrievalEngine(
        record_store=store,
        projection_coordinator=coordinator,
        search_projection_path=search_path,
    )
    return store, coordinator, recovery, engine, (sqlite_path, search_path, graph_path), (
        claim_ids[0],
        claim_ids[1],
    )


def _append_conflict(
    store: JsonlRecordStore,
    claim_ids: tuple[str, str],
    *,
    conflict_id: str,
) -> str:
    verification = store.verify()
    transaction = LedgerTransaction.create(
        sequence=(verification.last_sequence or 0) + 1,
        prev_hash=verification.last_valid_hash or GENESIS_HASH,
        actor=Actor(kind="system", id="destructive-test"),
        recorded_at=CONFLICT_AT,
        transaction_id=f"txn_{conflict_id}",
        events=(
            LedgerEvent(
                type="conflict.detected",
                payload={
                    "conflict_id": conflict_id,
                    "claim_ids": list(claim_ids),
                    "reason": "destructive recovery scenario",
                },
            ),
        ),
    )
    appended = store.append(transaction, expected_head=verification.last_valid_hash)
    return appended.new_head


class _FailingSearchProjectionAdapter(SearchProjectionAdapter):
    def rebuild_from_state(self, source: ReplayState) -> ProjectionBuildResult:
        del source
        raise RuntimeError("injected projection rebuild failure")


def test_partial_projection_rebuild_failure_is_fail_closed_and_recoverable(tmp_path: Path) -> None:
    store, coordinator, recovery, engine, paths, claim_ids = _runtime(tmp_path)
    new_head = _append_conflict(store, claim_ids, conflict_id="conflict_partial_rebuild")
    ledger_before = store.ledger_path.read_bytes()

    failing = ProjectionCoordinator(
        store,
        (
            SQLiteProjectionAdapter(paths[0]),
            _FailingSearchProjectionAdapter(paths[1]),
            GraphProjectionAdapter(paths[2]),
        ),
    )
    with pytest.raises(RuntimeError, match="injected projection rebuild failure"):
        failing.rebuild_all()

    assert store.ledger_path.read_bytes() == ledger_before
    assert store.verify().last_valid_hash == new_head
    status = coordinator.status()
    assert status.ready_for_query is False
    assert any(reason.startswith("search:") for reason in status.reason_codes)
    assert any(reason.startswith("graph:") for reason in status.reason_codes)
    with pytest.raises(ProjectionNotReadyError):
        engine.retrieve(RetrievalRequest(text="recovery"))

    result = recovery.rebuild(
        new_head,
        apply=True,
        confirmation=recovery.confirmation_token(new_head),
    )
    assert result.ready_after is True
    hits = engine.retrieve(RetrievalRequest(text="recovery projection", limit=10)).hits
    assert {item.claim_id for item in hits} == set(claim_ids)
    assert all(item.conflict_ids == ("conflict_partial_rebuild",) for item in hits)


def test_truncated_tail_repair_preserves_last_committed_query_semantics(tmp_path: Path) -> None:
    store, coordinator, recovery, engine, _, claim_ids = _runtime(tmp_path)
    baseline_head = str(store.verify().last_valid_hash)
    baseline_query = engine.retrieve(RetrievalRequest(text="recovery", limit=10)).to_dict()
    _append_conflict(store, claim_ids, conflict_id="conflict_truncated_tail")

    complete_bytes = store.ledger_path.read_bytes()
    damaged_bytes = complete_bytes[:-12]
    store.ledger_path.write_bytes(damaged_bytes)
    verification = store.verify()
    assert verification.valid is False
    assert verification.repairable_tail is True
    assert verification.last_valid_hash == baseline_head

    with pytest.raises(LedgerIntegrityError, match="invalid ledger"):
        recovery.rebuild(
            baseline_head,
            apply=True,
            confirmation=recovery.confirmation_token(baseline_head),
        )

    dry_run = store.repair_tail(dry_run=True)
    assert dry_run.applied is False
    assert dry_run.repairable is True
    assert dry_run.backup_path is None
    assert store.ledger_path.read_bytes() == damaged_bytes

    applied = store.repair_tail(dry_run=False)
    assert applied.applied is True
    assert applied.backup_path is not None
    assert applied.backup_path.read_bytes() == damaged_bytes
    assert store.verify().last_valid_hash == baseline_head
    assert coordinator.status().ready_for_query is True
    assert engine.retrieve(RetrievalRequest(text="recovery", limit=10)).to_dict() == baseline_query


def test_snapshot_restore_makes_newer_projections_stale_until_rebuilt(tmp_path: Path) -> None:
    store, coordinator, recovery, engine, _, claim_ids = _runtime(tmp_path)
    baseline_query = engine.retrieve(RetrievalRequest(text="recovery", limit=10)).to_dict()
    snapshot = store.snapshot()
    new_head = _append_conflict(store, claim_ids, conflict_id="conflict_snapshot_restore")
    coordinator.rebuild_all()
    assert coordinator.status().ready_for_query is True
    assert engine.retrieve(RetrievalRequest(text="recovery", limit=10)).to_dict() != baseline_query

    with pytest.raises(HeadMismatchError):
        store.restore_snapshot(snapshot.path, expected_head="sha256:" + "f" * 64, dry_run=False)

    preview = store.restore_snapshot(snapshot.path, expected_head=new_head, dry_run=True)
    assert preview.applied is False
    assert store.verify().last_valid_hash == new_head

    restored = store.restore_snapshot(snapshot.path, expected_head=new_head, dry_run=False)
    assert restored.applied is True
    assert restored.restored_head == snapshot.ledger_head
    assert restored.pre_restore_backup is not None
    assert (restored.pre_restore_backup / "events.jsonl").is_file()
    assert (restored.pre_restore_backup / "recovery.json").is_file()
    assert coordinator.status().ready_for_query is False
    with pytest.raises(ProjectionNotReadyError):
        engine.retrieve(RetrievalRequest(text="recovery"))

    restored_head = str(store.verify().last_valid_hash)
    rebuilt = recovery.rebuild(
        restored_head,
        apply=True,
        confirmation=recovery.confirmation_token(restored_head),
    )
    assert rebuilt.ready_after is True
    assert engine.retrieve(RetrievalRequest(text="recovery", limit=10)).to_dict() == baseline_query


def test_non_tail_corruption_is_never_automatically_removed(tmp_path: Path) -> None:
    store, _, recovery, _, paths, claim_ids = _runtime(tmp_path)
    current_head = _append_conflict(store, claim_ids, conflict_id="conflict_non_tail_damage")
    artifact_bytes = tuple(path.read_bytes() for path in paths)

    lines = store.ledger_path.read_bytes().splitlines(keepends=True)
    assert len(lines) >= 2
    lines[0] = b'{"schema":"broken"}\n'
    corrupted = b"".join(lines)
    store.ledger_path.write_bytes(corrupted)

    verification = store.verify()
    assert verification.valid is False
    assert verification.invalid_line_number == 1
    assert verification.repairable_tail is False

    with pytest.raises(LedgerIntegrityError, match="not confined to the final record"):
        store.repair_tail(dry_run=False)
    with pytest.raises(LedgerIntegrityError, match="invalid ledger"):
        recovery.rebuild(
            current_head,
            apply=True,
            confirmation=recovery.confirmation_token(current_head),
        )

    assert store.ledger_path.read_bytes() == corrupted
    assert tuple(path.read_bytes() for path in paths) == artifact_bytes
