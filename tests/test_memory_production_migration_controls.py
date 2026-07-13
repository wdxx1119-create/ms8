from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ms8.memory.application.legacy_migration import PreparedMigration, prepare_legacy_migration
from ms8.memory.application.production_migration import (
    BackupTarget,
    ProductionMigrationController,
    ProductionMigrationError,
    verify_prepared_migration,
)
from ms8.memory.application.replay import replay_transactions
from ms8.memory.domain.ledger import GENESIS_HASH, canonical_json
from ms8.memory.infrastructure.jsonl_ledger import JsonlRecordStore
from ms8.memory.runtime_format import (
    LEGACY_RUNTIME_FORMAT,
    RUNTIME_FORMAT_SCHEMA,
    RuntimeFormatManifest,
    load_runtime_format_manifest,
)

RECORDED_AT = "2026-07-12T06:00:00+00:00"


class _Coordinator:
    def __init__(
        self,
        store: JsonlRecordStore,
        *,
        before_status: Callable[[], None] | None = None,
    ) -> None:
        self.store = store
        self.before_status = before_status
        self.rebuild_count = 0

    def rebuild_all(self) -> Any:
        self.rebuild_count += 1
        state = replay_transactions(self.store.iterate())
        descriptor = SimpleNamespace(name="fake", built_from_ledger_head=state.ledger_head)
        projection = SimpleNamespace(descriptor=descriptor)
        return SimpleNamespace(
            ledger_head=state.ledger_head,
            last_sequence=state.last_sequence,
            logical_state_hash=state.logical_state_hash,
            projections=(projection,),
        )

    def require_ready_for_query(self) -> Any:
        if self.before_status is not None:
            callback = self.before_status
            self.before_status = None
            callback()
        state = replay_transactions(self.store.iterate())
        freshness = (SimpleNamespace(name="fake"),)
        return SimpleNamespace(
            ledger_valid=True,
            ledger_head=state.ledger_head,
            last_sequence=state.last_sequence,
            logical_state_hash=state.logical_state_hash,
            ready_for_query=True,
            freshness=freshness,
            reason_codes=(),
        )


def _rows() -> list[dict[str, object]]:
    return [
        {
            "id": "legacy-1",
            "text": "User prefers concise output",
            "normalized_text": "User prefers concise output",
            "category": "user_preference",
            "status": "accepted",
            "source": "ask",
            "created_at": "2026-07-01T01:02:03+00:00",
            "meta": {"confidence": 0.92},
            "scope": "personal",
            "authority": "user_explicit",
            "sensitivity": "private",
            "can_recall": True,
            "can_inject": True,
            "can_act_on": False,
            "unknown_extension": {"keep": True},
        },
        {
            "id": "legacy-2",
            "text": "Release strategy uses staged validation",
            "normalized_text": "Release strategy uses staged validation",
            "category": "product_decision",
            "status": "verified",
            "source": "system",
            "created_at": "2026-07-02T04:05:06+00:00",
            "meta": {"confidence": 0.88, "workspace_realm_id": "project:ms8"},
            "scope": "project",
            "authority": "system_observed",
            "sensitivity": "private",
            "can_recall": True,
            "can_inject": True,
            "can_act_on": False,
        },
    ]


def _prepared(rows: list[dict[str, object]] | None = None) -> PreparedMigration:
    return prepare_legacy_migration(
        _rows() if rows is None else rows,
        migration_id="mig_001",
        recorded_at=RECORDED_AT,
    )


def _controller(
    tmp_path: Path,
    *,
    before_status: Callable[[], None] | None = None,
) -> tuple[ProductionMigrationController, JsonlRecordStore, Path, Path, _Coordinator]:
    runtime_manifest = tmp_path / "runtime" / "memory-format.json"
    legacy_file = tmp_path / "legacy" / "records.jsonl"
    legacy_file.parent.mkdir(parents=True)
    legacy_file.write_text("legacy-authority\n", encoding="utf-8")
    legacy_dir = tmp_path / "legacy" / "indexes"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "index.json").write_text('{"version": 1}\n', encoding="utf-8")

    store = JsonlRecordStore(tmp_path / "ledger-v1")
    coordinator = _Coordinator(store, before_status=before_status)
    controller = ProductionMigrationController(
        runtime_manifest_path=runtime_manifest,
        backup_root=tmp_path / "migration-backups",
        record_store=store,
        projection_coordinator=coordinator,  # type: ignore[arg-type]
        backup_targets=(
            BackupTarget("legacy_records", legacy_file),
            BackupTarget("legacy_indexes", legacy_dir),
        ),
    )
    return controller, store, runtime_manifest, legacy_file, coordinator


def test_prepared_migration_integrity_rejects_tampered_plan() -> None:
    prepared = _prepared()
    verify_prepared_migration(prepared)

    tampered = PreparedMigration(
        plan=replace(prepared.plan, content_hash="sha256:" + "0" * 64),
        transactions=prepared.transactions,
    )
    with pytest.raises(ProductionMigrationError, match="content hash mismatch"):
        verify_prepared_migration(tampered)


def test_production_apply_dry_run_has_no_side_effects(tmp_path: Path) -> None:
    controller, store, runtime_manifest, legacy_file, _ = _controller(tmp_path)
    rows = _rows()
    prepared = _prepared(rows)

    result = controller.apply(
        prepared,
        rows,
        updated_at=RECORDED_AT,
        backup_id="backup_001",
    )

    assert result.applied is False
    assert result.dry_run is True
    assert result.backup is None
    assert result.target_manifest.active_format == "ledger-v1"
    assert runtime_manifest.exists() is False
    assert store.verify().transaction_count == 0
    assert legacy_file.read_text(encoding="utf-8") == "legacy-authority\n"
    assert (tmp_path / "migration-backups").exists() is False


def test_production_apply_creates_verified_backup_and_switches_manifest(tmp_path: Path) -> None:
    controller, store, runtime_manifest, legacy_file, coordinator = _controller(tmp_path)
    rows = _rows()
    prepared = _prepared(rows)

    result = controller.apply(
        prepared,
        rows,
        updated_at=RECORDED_AT,
        backup_id="backup_001",
        dry_run=False,
    )

    assert result.applied is True
    assert result.backup is not None
    verified_backup = controller.verify_backup(result.backup.path)
    assert verified_backup.backup_id == "backup_001"
    assert verified_backup.ledger_snapshot_head == GENESIS_HASH
    assert result.semantic_verification is not None
    assert result.semantic_verification.valid is True
    assert result.semantic_verification.transaction_count == 2
    assert result.semantic_verification.projection_names == ("fake",)
    assert coordinator.rebuild_count == 1

    manifest = load_runtime_format_manifest(runtime_manifest)
    assert manifest.active_format == "ledger-v1"
    assert manifest.previous_format == LEGACY_RUNTIME_FORMAT
    assert manifest.migration_id == "mig_001"
    assert manifest.ledger_head == store.verify().last_valid_hash
    assert store.verify().transaction_count == 2
    assert legacy_file.read_text(encoding="utf-8") == "legacy-authority\n"


def test_rollback_dry_run_and_apply_restore_legacy_authority(tmp_path: Path) -> None:
    controller, store, runtime_manifest, legacy_file, _ = _controller(tmp_path)
    rows = _rows()
    result = controller.apply(
        _prepared(rows),
        rows,
        updated_at=RECORDED_AT,
        backup_id="backup_001",
        dry_run=False,
    )
    assert result.backup is not None
    migrated_head = store.verify().last_valid_hash
    assert migrated_head is not None

    legacy_file.write_text("mutated-after-switch\n", encoding="utf-8")
    dry_run = controller.rollback(
        result.backup.path,
        expected_head=migrated_head,
    )
    assert dry_run.applied is False
    assert dry_run.restored_manifest.active_format == LEGACY_RUNTIME_FORMAT
    assert legacy_file.read_text(encoding="utf-8") == "mutated-after-switch\n"
    assert load_runtime_format_manifest(runtime_manifest).active_format == "ledger-v1"

    rolled_back = controller.rollback(
        result.backup.path,
        expected_head=migrated_head,
        dry_run=False,
    )
    assert rolled_back.applied is True
    assert rolled_back.restored_manifest.active_format == LEGACY_RUNTIME_FORMAT
    assert rolled_back.restored_ledger_head == GENESIS_HASH
    assert rolled_back.restored_sequence == 0
    assert legacy_file.read_text(encoding="utf-8") == "legacy-authority\n"
    assert runtime_manifest.exists() is False
    assert store.verify().transaction_count == 0


def test_backup_tampering_is_rejected(tmp_path: Path) -> None:
    controller, _, _, _, _ = _controller(tmp_path)
    backup = controller.create_full_backup(
        backup_id="backup_001",
        migration_id="mig_001",
        created_at=RECORDED_AT,
    )
    backed_up_file = backup.path / "legacy-files" / "legacy_records" / "file.bin"
    backed_up_file.write_text("tampered\n", encoding="utf-8")

    with pytest.raises(ProductionMigrationError, match="hash mismatch"):
        controller.verify_backup(backup.path)


def test_production_apply_rejects_ambiguous_legacy_booleans(tmp_path: Path) -> None:
    controller, store, runtime_manifest, _, _ = _controller(tmp_path)
    rows = _rows()
    rows[0]["can_inject"] = "false"
    prepared = _prepared(rows)

    with pytest.raises(ProductionMigrationError, match="must be a JSON boolean"):
        controller.apply(
            prepared,
            rows,
            updated_at=RECORDED_AT,
            backup_id="backup_001",
        )

    assert runtime_manifest.exists() is False
    assert store.verify().transaction_count == 0


def test_production_apply_rejects_source_drift(tmp_path: Path) -> None:
    controller, store, runtime_manifest, _, _ = _controller(tmp_path)
    original = _rows()
    prepared = _prepared(original)
    changed = _rows()
    changed[0]["text"] = "Changed after preparation"
    changed[0]["normalized_text"] = "Changed after preparation"

    with pytest.raises(ProductionMigrationError, match="source changed"):
        controller.apply(
            prepared,
            changed,
            updated_at=RECORDED_AT,
            backup_id="backup_001",
        )

    assert runtime_manifest.exists() is False
    assert store.verify().transaction_count == 0


def test_manifest_compare_and_swap_failure_restores_pre_migration_state(tmp_path: Path) -> None:
    runtime_manifest = tmp_path / "runtime" / "memory-format.json"

    def mutate_manifest() -> None:
        runtime_manifest.parent.mkdir(parents=True, exist_ok=True)
        changed = RuntimeFormatManifest(
            schema=RUNTIME_FORMAT_SCHEMA,
            active_format=LEGACY_RUNTIME_FORMAT,
            generation=1,
            updated_at="2026-07-12T06:01:00+00:00",
        )
        runtime_manifest.write_text(canonical_json(changed.to_dict()) + "\n", encoding="utf-8")

    controller, store, actual_manifest, legacy_file, _ = _controller(
        tmp_path,
        before_status=mutate_manifest,
    )
    assert actual_manifest == runtime_manifest
    rows = _rows()

    with pytest.raises(ProductionMigrationError, match="changed during migration"):
        controller.apply(
            _prepared(rows),
            rows,
            updated_at=RECORDED_AT,
            backup_id="backup_001",
            dry_run=False,
        )

    assert runtime_manifest.exists() is False
    assert store.verify().transaction_count == 0
    assert legacy_file.read_text(encoding="utf-8") == "legacy-authority\n"
    backup_metadata = json.loads(
        (tmp_path / "migration-backups" / "backup_001" / "backup.json").read_text(encoding="utf-8")
    )
    assert backup_metadata["runtime_manifest"]["present"] is False
