from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ms8.memory.application.legacy_migration import (
    LegacyMigrationStagingService,
    PreparedMigration,
    prepare_legacy_migration,
)
from ms8.memory.application.physical_purge import (
    PhysicalPurgeController,
    PhysicalPurgeError,
)
from ms8.memory.application.replay import replay_transactions
from ms8.memory.domain.ledger import canonical_json
from ms8.memory.infrastructure.jsonl_ledger import JsonlRecordStore
from ms8.memory.ports.record_store import HeadMismatchError
from ms8.memory.runtime_format import (
    LEDGER_V1_RUNTIME_FORMAT,
    LEGACY_RUNTIME_FORMAT,
    RUNTIME_FORMAT_SCHEMA,
    RuntimeFormatManifest,
    load_runtime_format_manifest,
)

RECORDED_AT = "2026-07-12T08:00:00+00:00"
PURGE_AT = "2026-07-12T08:30:00+00:00"


class _Coordinator:
    def __init__(self, store: JsonlRecordStore, *, fail_rebuild_once: bool = False) -> None:
        self.store = store
        self.fail_rebuild_once = fail_rebuild_once
        self.rebuild_count = 0

    def rebuild_all(self) -> Any:
        self.rebuild_count += 1
        if self.fail_rebuild_once:
            self.fail_rebuild_once = False
            raise RuntimeError("projection rebuild failed")
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
        state = replay_transactions(self.store.iterate())
        return SimpleNamespace(
            ledger_valid=True,
            ledger_head=state.ledger_head,
            last_sequence=state.last_sequence,
            logical_state_hash=state.logical_state_hash,
            ready_for_query=True,
            freshness=(SimpleNamespace(name="fake"),),
            reason_codes=(),
        )


def _rows() -> list[dict[str, object]]:
    return [
        {
            "id": "legacy-purge-1",
            "text": "Private preference scheduled for purge",
            "normalized_text": "Private preference scheduled for purge",
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
        },
        {
            "id": "legacy-retained-2",
            "text": "Retained product decision",
            "normalized_text": "Retained product decision",
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


def _prepared() -> PreparedMigration:
    return prepare_legacy_migration(
        _rows(),
        migration_id="mig_purge_001",
        recorded_at=RECORDED_AT,
    )


def _runtime(
    tmp_path: Path,
    *,
    fail_rebuild_once: bool = False,
) -> tuple[
    PhysicalPurgeController,
    JsonlRecordStore,
    Path,
    _Coordinator,
    PreparedMigration,
]:
    prepared = _prepared()
    store = JsonlRecordStore(tmp_path / "ledger-v1")
    LegacyMigrationStagingService(store).apply(prepared)
    head = store.verify().last_valid_hash
    assert head is not None

    runtime_manifest = tmp_path / "runtime" / "memory-format.json"
    runtime_manifest.parent.mkdir(parents=True)
    manifest = RuntimeFormatManifest(
        schema=RUNTIME_FORMAT_SCHEMA,
        active_format=LEDGER_V1_RUNTIME_FORMAT,
        generation=1,
        updated_at=RECORDED_AT,
        previous_format=LEGACY_RUNTIME_FORMAT,
        migration_id=prepared.plan.migration_id,
        ledger_head=head,
    )
    runtime_manifest.write_text(canonical_json(manifest.to_dict()) + "\n", encoding="utf-8")

    coordinator = _Coordinator(store, fail_rebuild_once=fail_rebuild_once)
    controller = PhysicalPurgeController(
        runtime_manifest_path=runtime_manifest,
        record_store=store,
        projection_coordinator=coordinator,  # type: ignore[arg-type]
        backup_root=tmp_path / "purge-backups",
        staging_root=tmp_path / "purge-staging",
        additional_backup_roots=(tmp_path / "external-export",),
    )
    return controller, store, runtime_manifest, coordinator, prepared


def test_purge_plan_is_deterministic_and_dry_run_has_no_side_effects(tmp_path: Path) -> None:
    controller, store, runtime_manifest, _, prepared = _runtime(tmp_path)
    claim_id = prepared.plan.previews[0].claim_id
    original_head = store.verify().last_valid_hash
    original_manifest = runtime_manifest.read_bytes()

    first = controller.plan([claim_id], purge_id="purge_001")
    second = controller.plan([claim_id], purge_id="purge_001")

    assert first == second
    assert first.requested_claim_ids == (claim_id,)
    assert first.expanded_claim_ids == (claim_id,)
    assert first.source_transaction_count == 2
    assert first.rewritten_transaction_count == 1
    assert first.dropped_transaction_count == 1
    assert first.retained_claim_count == 1
    assert first.target_ledger_head != first.source_ledger_head
    assert any("offline" in warning for warning in first.warnings)

    result = controller.apply(
        first,
        expected_head=first.source_ledger_head,
        updated_at=PURGE_AT,
    )
    assert result.applied is False
    assert result.dry_run is True
    assert store.verify().last_valid_hash == original_head
    assert runtime_manifest.read_bytes() == original_manifest
    assert (tmp_path / "purge-backups").exists() is False
    assert (tmp_path / "purge-staging").exists() is False


def test_purge_apply_rewrites_ledger_rebuilds_projections_and_reports_residuals(tmp_path: Path) -> None:
    controller, store, runtime_manifest, coordinator, prepared = _runtime(tmp_path)
    purged_claim = prepared.plan.previews[0].claim_id
    retained_claim = prepared.plan.previews[1].claim_id
    plan = controller.plan([purged_claim], purge_id="purge_001")

    result = controller.apply(
        plan,
        expected_head=plan.source_ledger_head,
        updated_at=PURGE_AT,
        confirmation="purge_001",
        dry_run=False,
    )

    assert result.applied is True
    assert result.dry_run is False
    assert coordinator.rebuild_count == 1
    state = replay_transactions(store.iterate())
    assert purged_claim not in state.claims
    assert set(state.claims) == {retained_claim}
    assert state.ledger_head == plan.target_ledger_head
    assert store.verify().transaction_count == 1

    manifest = load_runtime_format_manifest(runtime_manifest)
    assert manifest.active_format == LEDGER_V1_RUNTIME_FORMAT
    assert manifest.generation == 2
    assert manifest.ledger_head == plan.target_ledger_head
    assert manifest.migration_id == "mig_purge_001"

    assert result.backup_path is not None
    assert result.report_path is not None
    backup_ledger = result.backup_path / "ledger-snapshot" / "events.jsonl"
    assert purged_claim in backup_ledger.read_text(encoding="utf-8")
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["offline_backup_deletion_claimed"] is False
    assert report["deletion_scope"] == "active ledger and rebuilt projections only"
    kinds = {item["kind"] for item in report["residual_locations"]}
    assert kinds == {
        "managed_pre_purge_snapshot",
        "declared_additional_backup_root",
        "offline_or_user_controlled_backups",
    }
    assert (tmp_path / "purge-staging" / "purge_001").exists() is False


def test_purge_apply_requires_exact_confirmation_and_expected_head(tmp_path: Path) -> None:
    controller, store, runtime_manifest, _, prepared = _runtime(tmp_path)
    plan = controller.plan([prepared.plan.previews[0].claim_id], purge_id="purge_001")
    original_manifest = runtime_manifest.read_bytes()

    with pytest.raises(PhysicalPurgeError, match="confirmation"):
        controller.apply(
            plan,
            expected_head=plan.source_ledger_head,
            updated_at=PURGE_AT,
            confirmation="PURGE_001",
            dry_run=False,
        )
    with pytest.raises(HeadMismatchError):
        controller.apply(
            plan,
            expected_head="sha256:" + "f" * 64,
            updated_at=PURGE_AT,
        )

    assert store.verify().last_valid_hash == plan.source_ledger_head
    assert runtime_manifest.read_bytes() == original_manifest
    assert (tmp_path / "purge-backups").exists() is False


def test_purge_unknown_claim_and_manifest_head_mismatch_fail_closed(tmp_path: Path) -> None:
    controller, store, runtime_manifest, _, _ = _runtime(tmp_path)

    with pytest.raises(PhysicalPurgeError, match="unknown claim IDs"):
        controller.plan(["clm_unknown"], purge_id="purge_001")

    manifest = load_runtime_format_manifest(runtime_manifest)
    mismatched = RuntimeFormatManifest(
        schema=manifest.schema,
        active_format=manifest.active_format,
        generation=manifest.generation,
        updated_at=manifest.updated_at,
        previous_format=manifest.previous_format,
        migration_id=manifest.migration_id,
        ledger_head="sha256:" + "a" * 64,
    )
    runtime_manifest.write_text(canonical_json(mismatched.to_dict()) + "\n", encoding="utf-8")

    with pytest.raises(PhysicalPurgeError, match="manifest ledger head"):
        controller.plan([next(iter(replay_transactions(store.iterate()).claims))], purge_id="purge_002")


def test_projection_rebuild_failure_restores_ledger_and_manifest(tmp_path: Path) -> None:
    controller, store, runtime_manifest, coordinator, prepared = _runtime(
        tmp_path,
        fail_rebuild_once=True,
    )
    plan = controller.plan([prepared.plan.previews[0].claim_id], purge_id="purge_001")
    original_manifest = runtime_manifest.read_bytes()
    original_transactions = tuple(item.to_json_line() for item in store.iterate())

    with pytest.raises(RuntimeError, match="projection rebuild failed"):
        controller.apply(
            plan,
            expected_head=plan.source_ledger_head,
            updated_at=PURGE_AT,
            confirmation="purge_001",
            dry_run=False,
        )

    assert tuple(item.to_json_line() for item in store.iterate()) == original_transactions
    assert store.verify().last_valid_hash == plan.source_ledger_head
    assert runtime_manifest.read_bytes() == original_manifest
    assert coordinator.rebuild_count == 2
    assert (tmp_path / "purge-staging" / "purge_001").exists() is False
