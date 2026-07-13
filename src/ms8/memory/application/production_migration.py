"""Production-safe migration controls for the ledger-v1 authority switch.

The controller is explicit and dependency-injected. It never discovers or touches
a user runtime by itself. Dry-run is the default for apply and rollback.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..domain.ledger import GENESIS_HASH, canonical_json
from ..infrastructure.durable_io import atomic_write_bytes, fsync_directory
from ..ports.record_store import LedgerIntegrityError, RecordStore
from ..runtime_format import (
    LEDGER_V1_RUNTIME_FORMAT,
    LEGACY_RUNTIME_FORMAT,
    RuntimeFormatManifest,
    default_runtime_format_manifest,
    load_runtime_format_manifest,
)
from .legacy_migration import (
    LegacyMigrationStagingService,
    PreparedMigration,
    prepare_legacy_migration,
)
from .projection_service import ProjectionCoordinator
from .replay import ReplayIntegrityError, replay_transactions

_BACKUP_SCHEMA = "ms8.production-migration-backup.v1"
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_.-]+$")
_GOVERNANCE_FIELDS = ("can_recall", "can_inject", "can_act_on")


class ProductionMigrationError(RuntimeError):
    """Raised when a production migration safety invariant is violated."""


@dataclass(frozen=True, slots=True)
class BackupTarget:
    """One explicit legacy file or directory included in the full backup."""

    name: str
    path: Path

    def __post_init__(self) -> None:
        normalized = _require_safe_token(self.name, "backup target name")
        object.__setattr__(self, "name", normalized)
        object.__setattr__(self, "path", Path(self.path))


@dataclass(frozen=True, slots=True)
class BackupFileEntry:
    target: str
    relative_path: str
    size: int
    sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "target": self.target,
            "relative_path": self.relative_path,
            "size": self.size,
            "sha256": self.sha256,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> BackupFileEntry:
        size = payload.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ProductionMigrationError("backup file entry size is invalid")
        relative_path = _require_relative_path(payload.get("relative_path"))
        return cls(
            target=_require_safe_token(payload.get("target"), "backup entry target"),
            relative_path=relative_path,
            size=size,
            sha256=str(payload.get("sha256") or ""),
        )


@dataclass(frozen=True, slots=True)
class BackupTargetState:
    name: str
    source_path: str
    kind: str
    entries: tuple[BackupFileEntry, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "source_path": self.source_path,
            "kind": self.kind,
            "entries": [entry.to_dict() for entry in self.entries],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> BackupTargetState:
        raw_entries = payload.get("entries", [])
        if not isinstance(raw_entries, list):
            raise ProductionMigrationError("backup target entries must be an array")
        entries = tuple(
            BackupFileEntry.from_dict(item)
            for item in raw_entries
            if isinstance(item, Mapping)
        )
        if len(entries) != len(raw_entries):
            raise ProductionMigrationError("backup target contains an invalid entry")
        return cls(
            name=_require_safe_token(payload.get("name"), "backup target name"),
            source_path=str(payload.get("source_path") or ""),
            kind=str(payload.get("kind") or ""),
            entries=entries,
        )


@dataclass(frozen=True, slots=True)
class ProductionBackupRef:
    backup_id: str
    path: Path
    created_at: str
    migration_id: str
    runtime_manifest_present: bool
    runtime_manifest_hash: str | None
    ledger_snapshot_path: Path
    ledger_snapshot_head: str
    target_states: tuple[BackupTargetState, ...]


@dataclass(frozen=True, slots=True)
class SemanticVerificationResult:
    migration_id: str
    valid: bool
    source_count: int
    transaction_count: int
    ledger_head: str
    last_sequence: int
    logical_state_hash: str
    projection_names: tuple[str, ...]
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProductionApplyResult:
    applied: bool
    dry_run: bool
    migration_id: str
    previous_manifest: RuntimeFormatManifest
    target_manifest: RuntimeFormatManifest
    backup: ProductionBackupRef | None
    semantic_verification: SemanticVerificationResult | None


@dataclass(frozen=True, slots=True)
class ProductionRollbackResult:
    applied: bool
    dry_run: bool
    backup_id: str
    previous_active_format: str
    restored_manifest: RuntimeFormatManifest
    restored_ledger_head: str
    restored_sequence: int


def _require_safe_token(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if _SAFE_TOKEN.fullmatch(text) is None:
        raise ProductionMigrationError(
            f"{field_name} must use letters, numbers, dot, dash, or underscore"
        )
    return text


def _require_relative_path(value: object) -> str:
    text = str(value or "").strip()
    candidate = Path(text)
    if not text or candidate.is_absolute() or ".." in candidate.parts:
        raise ProductionMigrationError("backup relative path is invalid")
    return candidate.as_posix()


def _safe_child(root: Path, relative_path: str) -> Path:
    candidate = (root / _require_relative_path(relative_path)).resolve()
    resolved_root = root.resolve()
    if candidate == resolved_root or not candidate.is_relative_to(resolved_root):
        raise ProductionMigrationError("backup path escapes its root")
    return candidate


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _atomic_write(path: Path, data: bytes) -> None:
    atomic_write_bytes(path, data)


def _remove_path(path: Path) -> None:
    if path.is_symlink():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _require_regular_tree(path: Path) -> None:
    if path.is_symlink():
        raise ProductionMigrationError(f"symbolic links are not allowed in migration backups: {path}")
    if path.is_dir():
        for item in path.rglob("*"):
            if item.is_symlink():
                raise ProductionMigrationError(
                    f"symbolic links are not allowed in migration backups: {item}"
                )
            if not item.is_file() and not item.is_dir():
                raise ProductionMigrationError(f"unsupported backup filesystem entry: {item}")
    elif path.exists() and not path.is_file():
        raise ProductionMigrationError(f"unsupported backup target type: {path}")


def _plan_content_hash(prepared: PreparedMigration) -> str:
    material = {
        "schema": prepared.plan.schema,
        "migration_id": prepared.plan.migration_id,
        "recorded_at": prepared.plan.recorded_at,
        "source_count": prepared.plan.source_count,
        "previews": [preview.to_dict() for preview in prepared.plan.previews],
        "issues": [issue.to_dict() for issue in prepared.plan.issues],
        "transaction_hashes": [transaction.hash for transaction in prepared.transactions],
    }
    return _sha256_bytes(canonical_json(material).encode("utf-8"))


def verify_prepared_migration(prepared: PreparedMigration) -> None:
    """Recompute plan integrity and transaction-chain invariants before apply."""

    plan = prepared.plan
    if plan.rejected_count:
        raise ProductionMigrationError("migration plan contains rejected records")
    if plan.migratable_count != len(prepared.transactions):
        raise ProductionMigrationError("migration transaction count does not match plan")
    if len(plan.previews) != len(prepared.transactions):
        raise ProductionMigrationError("migration preview count does not match transactions")
    if plan.source_count != plan.migratable_count + plan.rejected_count:
        raise ProductionMigrationError("migration source accounting is inconsistent")
    if _plan_content_hash(prepared) != plan.content_hash:
        raise ProductionMigrationError("migration plan content hash mismatch")

    expected_prev_hash = GENESIS_HASH
    expected_sequence = 1
    for preview, transaction in zip(plan.previews, prepared.transactions, strict=True):
        if transaction.transaction_id != preview.transaction_id:
            raise ProductionMigrationError("migration preview transaction ID mismatch")
        verification = transaction.verify(
            expected_prev_hash=expected_prev_hash,
            expected_sequence=expected_sequence,
        )
        if not verification.valid:
            raise ProductionMigrationError(
                "prepared migration transaction chain is invalid: "
                + ",".join(verification.reason_codes)
            )
        expected_prev_hash = transaction.hash
        expected_sequence += 1


def verify_source_matches_prepared(
    source_records: Iterable[object],
    prepared: PreparedMigration,
) -> tuple[object, ...]:
    """Fail closed on ambiguous booleans or source/plan drift."""

    records = tuple(source_records)
    for index, row in enumerate(records, start=1):
        if not isinstance(row, Mapping):
            continue
        for field_name in _GOVERNANCE_FIELDS:
            if field_name in row and not isinstance(row.get(field_name), bool):
                raise ProductionMigrationError(
                    f"legacy record {index} field {field_name} must be a JSON boolean"
                )
    recomputed = prepare_legacy_migration(
        records,
        migration_id=prepared.plan.migration_id,
        recorded_at=prepared.plan.recorded_at,
    )
    verify_prepared_migration(recomputed)
    if recomputed.plan.content_hash != prepared.plan.content_hash:
        raise ProductionMigrationError("legacy source changed after migration preparation")
    expected_hashes = tuple(item.hash for item in prepared.transactions)
    recomputed_hashes = tuple(item.hash for item in recomputed.transactions)
    if recomputed_hashes != expected_hashes:
        raise ProductionMigrationError("legacy source transaction hashes changed after preparation")
    return records


class ProductionMigrationController:
    """Coordinate backup, semantic verification, authority switch, and rollback."""

    def __init__(
        self,
        *,
        runtime_manifest_path: Path,
        backup_root: Path,
        record_store: RecordStore,
        projection_coordinator: ProjectionCoordinator,
        backup_targets: Iterable[BackupTarget],
    ) -> None:
        self.runtime_manifest_path = Path(runtime_manifest_path)
        self.backup_root = Path(backup_root)
        self.record_store = record_store
        self.projection_coordinator = projection_coordinator
        self.backup_targets = tuple(backup_targets)
        names = [target.name for target in self.backup_targets]
        if len(names) != len(set(names)):
            raise ValueError("backup target names must be unique")
        backup_resolved = self.backup_root.resolve()
        for target in self.backup_targets:
            target_resolved = target.path.resolve()
            if target_resolved == backup_resolved or backup_resolved.is_relative_to(target_resolved):
                raise ValueError("backup_root must not be inside a backup target")

    def _copy_target_to_backup(
        self,
        target: BackupTarget,
        files_root: Path,
    ) -> BackupTargetState:
        source = target.path
        _require_regular_tree(source)
        destination = files_root / target.name
        if not source.exists():
            return BackupTargetState(target.name, str(source), "missing", ())
        if source.is_file():
            data = source.read_bytes()
            _atomic_write(destination / "file.bin", data)
            entry = BackupFileEntry(target.name, "file.bin", len(data), _sha256_bytes(data))
            return BackupTargetState(target.name, str(source), "file", (entry,))

        destination.mkdir(parents=True, exist_ok=False)
        entries: list[BackupFileEntry] = []
        for source_file in sorted(item for item in source.rglob("*") if item.is_file()):
            relative = source_file.relative_to(source)
            data = source_file.read_bytes()
            _atomic_write(destination / relative, data)
            entries.append(
                BackupFileEntry(target.name, relative.as_posix(), len(data), _sha256_bytes(data))
            )
        fsync_directory(destination)
        return BackupTargetState(target.name, str(source), "directory", tuple(entries))

    def _backup_metadata(self, backup: ProductionBackupRef) -> dict[str, object]:
        return {
            "schema": _BACKUP_SCHEMA,
            "backup_id": backup.backup_id,
            "created_at": backup.created_at,
            "migration_id": backup.migration_id,
            "runtime_manifest": {
                "path": str(self.runtime_manifest_path),
                "present": backup.runtime_manifest_present,
                "sha256": backup.runtime_manifest_hash,
            },
            "ledger_snapshot": {
                "relative_path": backup.ledger_snapshot_path.relative_to(backup.path).as_posix(),
                "ledger_head": backup.ledger_snapshot_head,
            },
            "targets": [state.to_dict() for state in backup.target_states],
        }

    def create_full_backup(
        self,
        *,
        backup_id: str,
        migration_id: str,
        created_at: str,
    ) -> ProductionBackupRef:
        """Create and verify a full pre-apply backup before any migration write."""

        normalized_backup_id = _require_safe_token(backup_id, "backup_id")
        normalized_migration_id = _require_safe_token(migration_id, "migration_id")
        backup_path = self.backup_root / normalized_backup_id
        if backup_path.exists():
            raise FileExistsError(f"backup path already exists: {backup_path}")
        backup_path.mkdir(parents=True, exist_ok=False)
        try:
            manifest_copy = backup_path / "runtime-format.manifest"
            manifest_present = self.runtime_manifest_path.is_file()
            manifest_hash: str | None = None
            if manifest_present:
                manifest_bytes = self.runtime_manifest_path.read_bytes()
                _atomic_write(manifest_copy, manifest_bytes)
                manifest_hash = _sha256_bytes(manifest_bytes)

            snapshot = self.record_store.snapshot()
            snapshot_path = backup_path / "ledger-snapshot"
            exported = self.record_store.export_snapshot(snapshot, snapshot_path)

            files_root = backup_path / "legacy-files"
            files_root.mkdir(parents=True, exist_ok=False)
            target_states = tuple(
                self._copy_target_to_backup(target, files_root) for target in self.backup_targets
            )
            backup = ProductionBackupRef(
                backup_id=normalized_backup_id,
                path=backup_path,
                created_at=created_at,
                migration_id=normalized_migration_id,
                runtime_manifest_present=manifest_present,
                runtime_manifest_hash=manifest_hash,
                ledger_snapshot_path=exported.path,
                ledger_snapshot_head=exported.ledger_head,
                target_states=target_states,
            )
            _atomic_write(
                backup_path / "backup.json",
                (canonical_json(self._backup_metadata(backup)) + "\n").encode("utf-8"),
            )
            fsync_directory(backup_path)
            return self.verify_backup(backup_path)
        except (OSError, RuntimeError, TypeError, ValueError):
            shutil.rmtree(backup_path, ignore_errors=True)
            raise

    def verify_backup(self, backup_path: Path) -> ProductionBackupRef:
        """Verify hashes, configured target identity, and the exported ledger snapshot."""

        root = Path(backup_path)
        metadata_path = root / "backup.json"
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProductionMigrationError("migration backup metadata is unreadable") from exc
        if not isinstance(payload, Mapping) or payload.get("schema") != _BACKUP_SCHEMA:
            raise ProductionMigrationError("migration backup metadata schema is invalid")

        manifest_payload = payload.get("runtime_manifest")
        snapshot_payload = payload.get("ledger_snapshot")
        raw_targets = payload.get("targets")
        if not isinstance(manifest_payload, Mapping):
            raise ProductionMigrationError("migration backup runtime manifest metadata is invalid")
        if not isinstance(snapshot_payload, Mapping):
            raise ProductionMigrationError("migration backup snapshot metadata is invalid")
        if not isinstance(raw_targets, list):
            raise ProductionMigrationError("migration backup targets metadata is invalid")

        present = manifest_payload.get("present") is True
        manifest_hash_raw = manifest_payload.get("sha256")
        manifest_hash = str(manifest_hash_raw) if manifest_hash_raw not in (None, "") else None
        manifest_copy = root / "runtime-format.manifest"
        if present:
            if not manifest_copy.is_file():
                raise ProductionMigrationError("migration backup is missing runtime-format manifest")
            if _sha256_bytes(manifest_copy.read_bytes()) != manifest_hash:
                raise ProductionMigrationError("migration backup runtime-format hash mismatch")
        elif manifest_copy.exists():
            raise ProductionMigrationError("unexpected runtime-format manifest in backup")

        configured = {target.name: str(target.path) for target in self.backup_targets}
        states = tuple(
            BackupTargetState.from_dict(item)
            for item in raw_targets
            if isinstance(item, Mapping)
        )
        if len(states) != len(raw_targets):
            raise ProductionMigrationError("migration backup contains an invalid target")
        if {state.name: state.source_path for state in states} != configured:
            raise ProductionMigrationError("migration backup targets do not match controller configuration")

        files_root = root / "legacy-files"
        for state in states:
            if state.kind not in {"missing", "file", "directory"}:
                raise ProductionMigrationError(f"unsupported migration backup target kind: {state.kind}")
            if state.kind == "missing" and state.entries:
                raise ProductionMigrationError("missing backup target cannot contain files")
            for entry in state.entries:
                if entry.target != state.name:
                    raise ProductionMigrationError("migration backup entry target mismatch")
                candidate = _safe_child(files_root / state.name, entry.relative_path)
                if not candidate.is_file():
                    raise ProductionMigrationError(f"migration backup file is missing: {candidate}")
                data = candidate.read_bytes()
                if len(data) != entry.size or _sha256_bytes(data) != entry.sha256:
                    raise ProductionMigrationError(f"migration backup file hash mismatch: {candidate}")

        snapshot_relative = _require_relative_path(snapshot_payload.get("relative_path"))
        snapshot_path = _safe_child(root, snapshot_relative)
        inspected = self.record_store.restore_snapshot(snapshot_path, dry_run=True)
        snapshot_head = str(snapshot_payload.get("ledger_head") or "")
        if inspected.restored_head != snapshot_head:
            raise ProductionMigrationError("migration backup snapshot head mismatch")

        return ProductionBackupRef(
            backup_id=_require_safe_token(payload.get("backup_id"), "backup_id"),
            path=root,
            created_at=str(payload.get("created_at") or ""),
            migration_id=_require_safe_token(payload.get("migration_id"), "migration_id"),
            runtime_manifest_present=present,
            runtime_manifest_hash=manifest_hash,
            ledger_snapshot_path=snapshot_path,
            ledger_snapshot_head=snapshot_head,
            target_states=states,
        )

    def verify_semantics(self, prepared: PreparedMigration) -> SemanticVerificationResult:
        """Verify ledger, replay, migration IDs/statuses, and all required projections."""

        verify_prepared_migration(prepared)
        verification = self.record_store.verify()
        if not verification.valid:
            raise LedgerIntegrityError(
                "migrated ledger is invalid: " + ",".join(verification.reason_codes)
            )
        transactions = tuple(self.record_store.iterate())
        expected_transaction_ids = tuple(item.transaction_id for item in prepared.transactions)
        actual_transaction_ids = tuple(item.transaction_id for item in transactions)
        if actual_transaction_ids != expected_transaction_ids:
            raise ProductionMigrationError("migrated transaction IDs do not match prepared migration")
        try:
            state = replay_transactions(transactions)
        except ReplayIntegrityError as exc:
            raise ProductionMigrationError("migrated ledger replay failed") from exc

        reasons: list[str] = []
        for preview in prepared.plan.previews:
            event = state.memory_events.get(preview.event_id)
            claim_view = state.claims.get(preview.claim_id)
            evidence = state.evidence.get(preview.evidence_id)
            decision = state.decisions.get(preview.decision_id)
            if event is None:
                reasons.append(f"missing_event:{preview.event_id}")
            if claim_view is None:
                reasons.append(f"missing_claim:{preview.claim_id}")
            elif claim_view.current_status != preview.mapped_status:
                reasons.append(f"claim_status_mismatch:{preview.claim_id}")
            if evidence is None:
                reasons.append(f"missing_evidence:{preview.evidence_id}")
            if decision is None:
                reasons.append(f"missing_decision:{preview.decision_id}")
            elif str(decision.policy.get("migration_id") or "") != prepared.plan.migration_id:
                reasons.append(f"decision_migration_id_mismatch:{preview.decision_id}")
        if reasons:
            raise ProductionMigrationError("semantic migration verification failed: " + ",".join(reasons))

        status = self.projection_coordinator.require_ready_for_query()
        if status.ledger_head != state.ledger_head:
            raise ProductionMigrationError("projection status ledger head mismatch")
        if status.logical_state_hash != state.logical_state_hash:
            raise ProductionMigrationError("projection status logical state mismatch")
        projection_names = tuple(item.name for item in status.freshness)
        return SemanticVerificationResult(
            migration_id=prepared.plan.migration_id,
            valid=True,
            source_count=prepared.plan.source_count,
            transaction_count=len(transactions),
            ledger_head=state.ledger_head,
            last_sequence=state.last_sequence,
            logical_state_hash=state.logical_state_hash,
            projection_names=projection_names,
        )

    def _write_runtime_manifest(
        self,
        manifest: RuntimeFormatManifest,
        *,
        expected: RuntimeFormatManifest,
    ) -> None:
        current = load_runtime_format_manifest(self.runtime_manifest_path)
        if current != expected:
            raise ProductionMigrationError("runtime-format manifest changed during migration")
        _atomic_write(
            self.runtime_manifest_path,
            (canonical_json(manifest.to_dict()) + "\n").encode("utf-8"),
        )
        loaded = load_runtime_format_manifest(self.runtime_manifest_path)
        if loaded != manifest:
            raise ProductionMigrationError("runtime-format manifest switch verification failed")

    def _restore_target(self, state: BackupTargetState, backup: ProductionBackupRef) -> None:
        configured = {target.name: target.path for target in self.backup_targets}
        destination = configured[state.name]
        if destination.exists() or destination.is_symlink():
            _remove_path(destination)
        if state.kind == "missing":
            return
        source_root = backup.path / "legacy-files" / state.name
        if state.kind == "file":
            if len(state.entries) != 1 or state.entries[0].relative_path != "file.bin":
                raise ProductionMigrationError("file backup target metadata is invalid")
            _atomic_write(destination, (source_root / "file.bin").read_bytes())
            return
        destination.mkdir(parents=True, exist_ok=False)
        for entry in state.entries:
            source = _safe_child(source_root, entry.relative_path)
            _atomic_write(destination / Path(entry.relative_path), source.read_bytes())
        fsync_directory(destination)

    def _restore_runtime_manifest(self, backup: ProductionBackupRef) -> RuntimeFormatManifest:
        if backup.runtime_manifest_present:
            _atomic_write(
                self.runtime_manifest_path,
                (backup.path / "runtime-format.manifest").read_bytes(),
            )
        else:
            self.runtime_manifest_path.unlink(missing_ok=True)
            fsync_directory(self.runtime_manifest_path.parent)
        return load_runtime_format_manifest(self.runtime_manifest_path)

    def apply(
        self,
        prepared: PreparedMigration,
        source_records: Iterable[object],
        *,
        updated_at: str,
        backup_id: str,
        dry_run: bool = True,
    ) -> ProductionApplyResult:
        """Apply to an empty ledger and switch authority only after verification."""

        verify_prepared_migration(prepared)
        verify_source_matches_prepared(source_records, prepared)
        for target in self.backup_targets:
            _require_regular_tree(target.path)
        previous_manifest = load_runtime_format_manifest(self.runtime_manifest_path)
        if previous_manifest.active_format != LEGACY_RUNTIME_FORMAT:
            raise ProductionMigrationError("production apply requires legacy runtime format")
        verification = self.record_store.verify()
        current_head = verification.last_valid_hash or GENESIS_HASH
        if not verification.valid:
            raise LedgerIntegrityError("target ledger is invalid before migration")
        if verification.transaction_count != 0 or current_head != GENESIS_HASH:
            raise ProductionMigrationError("production migration target ledger must be empty")

        planned_head = prepared.transactions[-1].hash if prepared.transactions else GENESIS_HASH
        target_manifest = RuntimeFormatManifest(
            schema=previous_manifest.schema,
            active_format=LEDGER_V1_RUNTIME_FORMAT,
            generation=previous_manifest.generation + 1,
            updated_at=updated_at,
            previous_format=previous_manifest.active_format,
            migration_id=prepared.plan.migration_id,
            ledger_head=planned_head,
        )
        if dry_run:
            return ProductionApplyResult(
                applied=False,
                dry_run=True,
                migration_id=prepared.plan.migration_id,
                previous_manifest=previous_manifest,
                target_manifest=target_manifest,
                backup=None,
                semantic_verification=None,
            )

        backup = self.create_full_backup(
            backup_id=backup_id,
            migration_id=prepared.plan.migration_id,
            created_at=updated_at,
        )
        semantic: SemanticVerificationResult | None = None
        try:
            LegacyMigrationStagingService(
                self.record_store,
                self.projection_coordinator,
            ).apply(prepared)
            semantic = self.verify_semantics(prepared)
            if semantic.ledger_head != target_manifest.ledger_head:
                raise ProductionMigrationError("verified ledger head does not match target manifest")
            self._write_runtime_manifest(target_manifest, expected=previous_manifest)
        except (OSError, RuntimeError, TypeError, ValueError):
            self.record_store.restore_snapshot(backup.ledger_snapshot_path, dry_run=False)
            self.projection_coordinator.rebuild_all()
            self._restore_runtime_manifest(backup)
            raise
        if semantic is None:
            raise ProductionMigrationError("semantic verification did not complete")
        return ProductionApplyResult(
            applied=True,
            dry_run=False,
            migration_id=prepared.plan.migration_id,
            previous_manifest=previous_manifest,
            target_manifest=target_manifest,
            backup=backup,
            semantic_verification=semantic,
        )

    def rollback(
        self,
        backup_path: Path,
        *,
        expected_head: str | None = None,
        dry_run: bool = True,
    ) -> ProductionRollbackResult:
        """Restore legacy authority and the verified pre-migration ledger snapshot."""

        backup = self.verify_backup(backup_path)
        current_manifest = load_runtime_format_manifest(self.runtime_manifest_path)
        if backup.runtime_manifest_present:
            raw_manifest = json.loads(
                (backup.path / "runtime-format.manifest").read_text(encoding="utf-8")
            )
            if not isinstance(raw_manifest, Mapping):
                raise ProductionMigrationError("backup runtime-format manifest is invalid")
            restored_manifest = RuntimeFormatManifest.from_dict(raw_manifest)
        else:
            restored_manifest = default_runtime_format_manifest()

        inspected = self.record_store.restore_snapshot(
            backup.ledger_snapshot_path,
            expected_head=expected_head,
            dry_run=True,
        )
        if dry_run:
            return ProductionRollbackResult(
                applied=False,
                dry_run=True,
                backup_id=backup.backup_id,
                previous_active_format=current_manifest.active_format,
                restored_manifest=restored_manifest,
                restored_ledger_head=inspected.restored_head,
                restored_sequence=inspected.restored_sequence,
            )

        for state in backup.target_states:
            self._restore_target(state, backup)
        restored_manifest = self._restore_runtime_manifest(backup)
        restored = self.record_store.restore_snapshot(
            backup.ledger_snapshot_path,
            expected_head=expected_head,
            dry_run=False,
        )
        self.projection_coordinator.rebuild_all()
        return ProductionRollbackResult(
            applied=True,
            dry_run=False,
            backup_id=backup.backup_id,
            previous_active_format=current_manifest.active_format,
            restored_manifest=restored_manifest,
            restored_ledger_head=restored.restored_head,
            restored_sequence=restored.restored_sequence,
        )


__all__ = [
    "BackupFileEntry",
    "BackupTarget",
    "BackupTargetState",
    "ProductionApplyResult",
    "ProductionBackupRef",
    "ProductionMigrationController",
    "ProductionMigrationError",
    "ProductionRollbackResult",
    "SemanticVerificationResult",
    "verify_prepared_migration",
    "verify_source_matches_prepared",
]
