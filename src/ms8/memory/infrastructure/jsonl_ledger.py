"""Durable JSONL implementation of the authoritative ledger RecordStore."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..domain.ledger import GENESIS_HASH, LEDGER_SCHEMA, LedgerTransaction, canonical_json
from ..ports.record_store import (
    AppendResult,
    HeadMismatchError,
    LedgerIntegrityError,
    LedgerVerification,
    RestoreResult,
    SnapshotRef,
    TailRepairResult,
)
from .durable_io import atomic_write_bytes, exclusive_file_lock, fsync_directory

MANIFEST_SCHEMA = "ms8.ledger.manifest.v1"
SNAPSHOT_SCHEMA = "ms8.ledger.snapshot.v1"
RECOVERY_SCHEMA = "ms8.ledger.recovery.v1"

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp_token() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _atomic_write(path: Path, data: bytes) -> None:
    atomic_write_bytes(path, data)


def _copy_file_durable(source: Path, destination: Path) -> None:
    _atomic_write(destination, source.read_bytes())


def _read_json_object(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


@dataclass(frozen=True, slots=True)
class _ScanResult:
    transactions: tuple[LedgerTransaction, ...]
    verification: LedgerVerification


class JsonlRecordStore:
    """Append-only transaction ledger with a rebuildable manifest.

    ``events.jsonl`` is the authority. ``manifest.json`` is only a durable head
    cache and is reconciled from the ledger whenever it is missing or stale.
    Damaged data is never removed implicitly; tail repair and snapshot restore
    are explicit, dry-run-by-default maintenance operations.
    """

    def __init__(self, memory_root: Path):
        self.memory_root = Path(memory_root)
        self.ledger_dir = self.memory_root / "ledger"
        self.ledger_path = self.ledger_dir / "events.jsonl"
        self.manifest_path = self.ledger_dir / "manifest.json"
        self.lock_path = self.ledger_dir / ".ledger.lock"
        self.snapshots_dir = self.memory_root / "snapshots"
        self.recovery_dir = self.memory_root / "audit" / "ledger_recovery"
        self.ledger_dir.mkdir(parents=True, exist_ok=True)
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        with exclusive_file_lock(self.lock_path):
            yield

    def _scan_path(self, ledger_path: Path) -> _ScanResult:
        transactions: list[LedgerTransaction] = []
        reasons: list[str] = []
        truncated_tail = False
        invalid_line_number: int | None = None
        repairable_tail = False
        expected_hash = GENESIS_HASH
        expected_sequence = 1
        valid_bytes = 0
        total_bytes = ledger_path.stat().st_size if ledger_path.exists() else 0

        if ledger_path.exists():
            with ledger_path.open("rb") as handle:
                line_number = 0
                while True:
                    raw_line = handle.readline()
                    if not raw_line:
                        break
                    line_number += 1
                    line_end = handle.tell()
                    is_final_line = line_end == total_bytes
                    if not raw_line.endswith(b"\n"):
                        truncated_tail = True
                        invalid_line_number = line_number
                        repairable_tail = is_final_line
                        reasons.append(f"line_{line_number}:truncated_tail")
                        break
                    payload_bytes = raw_line[:-1]
                    if payload_bytes.endswith(b"\r"):
                        payload_bytes = payload_bytes[:-1]
                    if not payload_bytes:
                        invalid_line_number = line_number
                        repairable_tail = is_final_line
                        reasons.append(f"line_{line_number}:blank_transaction")
                        break
                    try:
                        line = payload_bytes.decode("utf-8")
                    except UnicodeDecodeError:
                        invalid_line_number = line_number
                        repairable_tail = is_final_line
                        reasons.append(f"line_{line_number}:invalid_utf8")
                        break
                    try:
                        transaction = LedgerTransaction.from_json_line(line)
                    except (TypeError, ValueError):
                        invalid_line_number = line_number
                        repairable_tail = is_final_line
                        reasons.append(f"line_{line_number}:invalid_transaction")
                        break
                    tx_verification = transaction.verify(
                        expected_prev_hash=expected_hash,
                        expected_sequence=expected_sequence,
                    )
                    if not tx_verification.valid:
                        invalid_line_number = line_number
                        repairable_tail = is_final_line
                        reasons.extend(
                            f"line_{line_number}:{reason}" for reason in tx_verification.reason_codes
                        )
                        break
                    transactions.append(transaction)
                    expected_hash = transaction.hash
                    expected_sequence += 1
                    valid_bytes = line_end

        first_sequence = transactions[0].sequence if transactions else None
        last_sequence = transactions[-1].sequence if transactions else None
        damaged_bytes = max(0, total_bytes - valid_bytes) if reasons else 0
        ledger_verification = LedgerVerification(
            valid=not reasons,
            transaction_count=len(transactions),
            first_sequence=first_sequence,
            last_sequence=last_sequence,
            last_valid_hash=expected_hash,
            invalid_sequence=expected_sequence if reasons else None,
            reason_codes=tuple(reasons),
            truncated_tail_detected=truncated_tail,
            invalid_line_number=invalid_line_number,
            valid_bytes=valid_bytes,
            total_bytes=total_bytes,
            damaged_bytes=damaged_bytes,
            repairable_tail=repairable_tail,
        )
        return _ScanResult(transactions=tuple(transactions), verification=ledger_verification)

    def _scan(self) -> _ScanResult:
        return self._scan_path(self.ledger_path)

    def _manifest_payload(self, scan: _ScanResult) -> dict[str, Any]:
        verification = scan.verification
        return {
            "schema": MANIFEST_SCHEMA,
            "ledger_schema": LEDGER_SCHEMA,
            "head_hash": verification.last_valid_hash or GENESIS_HASH,
            "last_sequence": verification.last_sequence or 0,
            "transaction_count": verification.transaction_count,
            "updated_at": _utc_now(),
        }

    def _read_manifest(self) -> dict[str, Any] | None:
        payload = _read_json_object(self.manifest_path)
        if payload is None:
            return None
        if payload.get("schema") != MANIFEST_SCHEMA or payload.get("ledger_schema") != LEDGER_SCHEMA:
            return None
        return payload

    def _write_manifest(self, scan: _ScanResult) -> None:
        payload = self._manifest_payload(scan)
        _atomic_write(self.manifest_path, (canonical_json(payload) + "\n").encode("utf-8"))

    def _reconcile_manifest(self, scan: _ScanResult) -> None:
        if not scan.verification.valid:
            return
        current = self._read_manifest()
        expected = self._manifest_payload(scan)
        stable_keys = ("schema", "ledger_schema", "head_hash", "last_sequence", "transaction_count")
        if current is None or any(current.get(key) != expected.get(key) for key in stable_keys):
            self._write_manifest(scan)

    @staticmethod
    def _assert_manifest_matches_scan(manifest: dict[str, Any], scan: _ScanResult) -> None:
        expected = {
            "schema": MANIFEST_SCHEMA,
            "ledger_schema": LEDGER_SCHEMA,
            "head_hash": scan.verification.last_valid_hash or GENESIS_HASH,
            "last_sequence": scan.verification.last_sequence or 0,
            "transaction_count": scan.verification.transaction_count,
        }
        mismatches = [key for key, value in expected.items() if manifest.get(key) != value]
        if mismatches:
            raise LedgerIntegrityError("snapshot manifest mismatch: " + ",".join(mismatches))

    def append(self, transaction: LedgerTransaction, expected_head: str | None = None) -> AppendResult:
        with self._exclusive_lock():
            scan = self._scan()
            if not scan.verification.valid:
                raise LedgerIntegrityError(
                    "ledger verification failed before append: " + ",".join(scan.verification.reason_codes)
                )
            self._reconcile_manifest(scan)
            current_head = scan.verification.last_valid_hash or GENESIS_HASH
            current_sequence = scan.verification.last_sequence or 0
            if expected_head is not None and expected_head != current_head:
                raise HeadMismatchError(f"expected head {expected_head}, current head {current_head}")
            if any(item.transaction_id == transaction.transaction_id for item in scan.transactions):
                raise LedgerIntegrityError(f"duplicate transaction_id: {transaction.transaction_id}")
            transaction_check = transaction.verify(
                expected_prev_hash=current_head,
                expected_sequence=current_sequence + 1,
            )
            if not transaction_check.valid:
                raise LedgerIntegrityError("invalid append transaction: " + ",".join(transaction_check.reason_codes))

            encoded = (transaction.to_json_line() + "\n").encode("utf-8")
            self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
            with self.ledger_path.open("ab") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            fsync_directory(self.ledger_path.parent)

            updated_transactions = (*scan.transactions, transaction)
            updated_verification = LedgerVerification(
                valid=True,
                transaction_count=len(updated_transactions),
                first_sequence=updated_transactions[0].sequence,
                last_sequence=transaction.sequence,
                last_valid_hash=transaction.hash,
                invalid_sequence=None,
                reason_codes=(),
                truncated_tail_detected=False,
                invalid_line_number=None,
                valid_bytes=scan.verification.valid_bytes + len(encoded),
                total_bytes=scan.verification.total_bytes + len(encoded),
                damaged_bytes=0,
                repairable_tail=False,
            )
            updated_scan = _ScanResult(
                transactions=updated_transactions,
                verification=updated_verification,
            )
            self._write_manifest(updated_scan)
            return AppendResult(
                transaction_id=transaction.transaction_id,
                sequence=transaction.sequence,
                previous_head=current_head,
                new_head=transaction.hash,
                ledger_path=self.ledger_path,
                durable=True,
            )

    def iterate(self, after_sequence: int = 0) -> Iterator[LedgerTransaction]:
        if isinstance(after_sequence, bool) or not isinstance(after_sequence, int) or after_sequence < 0:
            raise ValueError("after_sequence must be a non-negative integer")
        with self._exclusive_lock():
            scan = self._scan()
            self._reconcile_manifest(scan)
            accepted_prefix = tuple(item for item in scan.transactions if item.sequence > after_sequence)
        return iter(accepted_prefix)

    def verify(self) -> LedgerVerification:
        with self._exclusive_lock():
            scan = self._scan()
            self._reconcile_manifest(scan)
            return scan.verification

    def snapshot(self) -> SnapshotRef:
        with self._exclusive_lock():
            scan = self._scan()
            if not scan.verification.valid:
                raise LedgerIntegrityError(
                    "cannot snapshot invalid ledger: " + ",".join(scan.verification.reason_codes)
                )
            self._reconcile_manifest(scan)
            snapshot_id = "snapshot_" + _timestamp_token()
            snapshot_dir = self.snapshots_dir / snapshot_id
            snapshot_dir.mkdir(parents=True, exist_ok=False)
            ledger_copy = snapshot_dir / "events.jsonl"
            manifest_copy = snapshot_dir / "manifest.json"
            metadata_path = snapshot_dir / "snapshot.json"
            if self.ledger_path.exists():
                _copy_file_durable(self.ledger_path, ledger_copy)
            else:
                _atomic_write(ledger_copy, b"")
            _copy_file_durable(self.manifest_path, manifest_copy)
            created_at = _utc_now()
            manifest_bytes = manifest_copy.read_bytes()
            metadata: dict[str, Any] = {
                "schema": SNAPSHOT_SCHEMA,
                "snapshot_id": snapshot_id,
                "created_at": created_at,
                "ledger_head": scan.verification.last_valid_hash or GENESIS_HASH,
                "last_sequence": scan.verification.last_sequence or 0,
                "transaction_count": scan.verification.transaction_count,
                "ledger_hash": _sha256_bytes(ledger_copy.read_bytes()),
                "manifest_hash": _sha256_bytes(manifest_bytes),
            }
            _atomic_write(metadata_path, (canonical_json(metadata) + "\n").encode("utf-8"))
            fsync_directory(snapshot_dir)
            return SnapshotRef(
                snapshot_id=snapshot_id,
                path=snapshot_dir,
                ledger_head=metadata["ledger_head"],
                last_sequence=metadata["last_sequence"],
                created_at=created_at,
                manifest_hash=metadata["manifest_hash"],
            )

    def _validate_snapshot(self, snapshot_path: Path) -> tuple[SnapshotRef, _ScanResult]:
        root = Path(snapshot_path)
        ledger = root / "events.jsonl"
        manifest_path = root / "manifest.json"
        metadata_path = root / "snapshot.json"
        if not ledger.is_file() or not manifest_path.is_file() or not metadata_path.is_file():
            raise LedgerIntegrityError("snapshot is missing events.jsonl, manifest.json, or snapshot.json")
        scan = self._scan_path(ledger)
        if not scan.verification.valid:
            raise LedgerIntegrityError("snapshot ledger is invalid: " + ",".join(scan.verification.reason_codes))
        manifest = _read_json_object(manifest_path)
        if manifest is None:
            raise LedgerIntegrityError("snapshot manifest is invalid")
        self._assert_manifest_matches_scan(manifest, scan)
        metadata = _read_json_object(metadata_path)
        if metadata is None or metadata.get("schema") != SNAPSHOT_SCHEMA:
            raise LedgerIntegrityError("snapshot metadata is invalid")
        ledger_hash = _sha256_bytes(ledger.read_bytes())
        manifest_hash = _sha256_bytes(manifest_path.read_bytes())
        expected_head = scan.verification.last_valid_hash or GENESIS_HASH
        expected_sequence = scan.verification.last_sequence or 0
        checks: dict[str, Any] = {
            "ledger_hash": ledger_hash,
            "manifest_hash": manifest_hash,
            "ledger_head": expected_head,
            "last_sequence": expected_sequence,
            "transaction_count": scan.verification.transaction_count,
        }
        mismatches = [key for key, value in checks.items() if metadata.get(key) != value]
        if mismatches:
            raise LedgerIntegrityError("snapshot metadata mismatch: " + ",".join(mismatches))
        snapshot_id = str(metadata.get("snapshot_id") or root.name)
        created_at = str(metadata.get("created_at") or "")
        return (
            SnapshotRef(
                snapshot_id=snapshot_id,
                path=root,
                ledger_head=expected_head,
                last_sequence=expected_sequence,
                created_at=created_at,
                manifest_hash=manifest_hash,
            ),
            scan,
        )

    def export_snapshot(self, snapshot: SnapshotRef, destination: Path) -> SnapshotRef:
        with self._exclusive_lock():
            validated, _scan = self._validate_snapshot(snapshot.path)
            if validated.ledger_head != snapshot.ledger_head or validated.last_sequence != snapshot.last_sequence:
                raise LedgerIntegrityError("snapshot reference does not match snapshot contents")
            target = Path(destination)
            if target.exists():
                raise FileExistsError(f"snapshot export destination already exists: {target}")
            target.parent.mkdir(parents=True, exist_ok=True)
            staging = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
            try:
                shutil.copytree(snapshot.path, staging)
                for path in staging.iterdir():
                    if path.is_file():
                        with path.open("rb") as handle:
                            try:
                                os.fsync(handle.fileno())
                            except OSError:
                                continue
                fsync_directory(staging)
                os.replace(staging, target)
                fsync_directory(target.parent)
            finally:
                if staging.exists():
                    shutil.rmtree(staging, ignore_errors=True)
            exported, _ = self._validate_snapshot(target)
            return exported

    def repair_tail(self, *, dry_run: bool = True) -> TailRepairResult:
        with self._exclusive_lock():
            scan = self._scan()
            verification = scan.verification
            current_head = verification.last_valid_hash or GENESIS_HASH
            current_sequence = verification.last_sequence or 0
            if verification.valid:
                return TailRepairResult(
                    applied=False,
                    repairable=False,
                    removed_bytes=0,
                    backup_path=None,
                    previous_reason_codes=(),
                    last_valid_sequence=current_sequence,
                    last_valid_hash=current_head,
                )
            if not verification.repairable_tail:
                raise LedgerIntegrityError("ledger damage is not confined to the final record")
            if dry_run:
                return TailRepairResult(
                    applied=False,
                    repairable=True,
                    removed_bytes=verification.damaged_bytes,
                    backup_path=None,
                    previous_reason_codes=verification.reason_codes,
                    last_valid_sequence=current_sequence,
                    last_valid_hash=current_head,
                )

            original = self.ledger_path.read_bytes()
            recovery_id = "tail_repair_" + _timestamp_token()
            backup_path = self.recovery_dir / f"{recovery_id}.events.jsonl"
            report_path = self.recovery_dir / f"{recovery_id}.json"
            _atomic_write(backup_path, original)
            report = {
                "schema": RECOVERY_SCHEMA,
                "operation": "repair_tail",
                "applied_at": _utc_now(),
                "ledger_path": str(self.ledger_path),
                "backup_path": str(backup_path),
                "original_hash": _sha256_bytes(original),
                "valid_bytes": verification.valid_bytes,
                "removed_bytes": verification.damaged_bytes,
                "last_valid_sequence": current_sequence,
                "last_valid_hash": current_head,
                "reason_codes": list(verification.reason_codes),
            }
            _atomic_write(report_path, (canonical_json(report) + "\n").encode("utf-8"))
            try:
                with self.ledger_path.open("r+b") as handle:
                    handle.truncate(verification.valid_bytes)
                    handle.flush()
                    os.fsync(handle.fileno())
                fsync_directory(self.ledger_path.parent)
                repaired = self._scan()
                if not repaired.verification.valid:
                    raise LedgerIntegrityError("tail repair did not produce a valid ledger")
                self._write_manifest(repaired)
            except (OSError, RuntimeError, TypeError, ValueError, KeyError):
                _atomic_write(self.ledger_path, original)
                raise
            return TailRepairResult(
                applied=True,
                repairable=True,
                removed_bytes=verification.damaged_bytes,
                backup_path=backup_path,
                previous_reason_codes=verification.reason_codes,
                last_valid_sequence=current_sequence,
                last_valid_hash=current_head,
            )

    def _create_pre_restore_backup(self, scan: _ScanResult) -> Path:
        backup_dir = self.snapshots_dir / ("pre_restore_" + _timestamp_token())
        backup_dir.mkdir(parents=True, exist_ok=False)
        ledger_bytes = self.ledger_path.read_bytes() if self.ledger_path.exists() else b""
        _atomic_write(backup_dir / "events.jsonl", ledger_bytes)
        manifest_present = self.manifest_path.exists()
        if manifest_present:
            _copy_file_durable(self.manifest_path, backup_dir / "manifest.json")
        metadata = {
            "schema": RECOVERY_SCHEMA,
            "operation": "pre_restore_backup",
            "created_at": _utc_now(),
            "ledger_hash": _sha256_bytes(ledger_bytes),
            "manifest_present": manifest_present,
            "ledger_valid": scan.verification.valid,
            "last_valid_hash": scan.verification.last_valid_hash or GENESIS_HASH,
            "last_valid_sequence": scan.verification.last_sequence or 0,
            "reason_codes": list(scan.verification.reason_codes),
        }
        _atomic_write(backup_dir / "recovery.json", (canonical_json(metadata) + "\n").encode("utf-8"))
        fsync_directory(backup_dir)
        return backup_dir

    def restore_snapshot(
        self,
        snapshot_path: Path,
        *,
        expected_head: str | None = None,
        dry_run: bool = True,
    ) -> RestoreResult:
        with self._exclusive_lock():
            snapshot, snapshot_scan = self._validate_snapshot(Path(snapshot_path))
            current_scan = self._scan()
            current_head = current_scan.verification.last_valid_hash or GENESIS_HASH
            if expected_head is not None and expected_head != current_head:
                raise HeadMismatchError(f"expected head {expected_head}, current head {current_head}")
            if dry_run:
                return RestoreResult(
                    applied=False,
                    snapshot_path=snapshot.path,
                    previous_head=current_head,
                    restored_head=snapshot.ledger_head,
                    restored_sequence=snapshot.last_sequence,
                    pre_restore_backup=None,
                )

            previous_ledger = self.ledger_path.read_bytes() if self.ledger_path.exists() else None
            previous_manifest = self.manifest_path.read_bytes() if self.manifest_path.exists() else None
            backup_dir = self._create_pre_restore_backup(current_scan)
            try:
                _atomic_write(self.ledger_path, (snapshot.path / "events.jsonl").read_bytes())
                _atomic_write(self.manifest_path, (snapshot.path / "manifest.json").read_bytes())
                restored = self._scan()
                if not restored.verification.valid:
                    raise LedgerIntegrityError("restored ledger failed verification")
                if restored.verification.last_valid_hash != snapshot.ledger_head:
                    raise LedgerIntegrityError("restored ledger head does not match snapshot")
                self._assert_manifest_matches_scan(_read_json_object(self.manifest_path) or {}, restored)
                self._reconcile_manifest(restored)
            except (OSError, RuntimeError, TypeError, ValueError, KeyError):
                if previous_ledger is None:
                    self.ledger_path.unlink(missing_ok=True)
                else:
                    _atomic_write(self.ledger_path, previous_ledger)
                if previous_manifest is None:
                    self.manifest_path.unlink(missing_ok=True)
                else:
                    _atomic_write(self.manifest_path, previous_manifest)
                raise
            return RestoreResult(
                applied=True,
                snapshot_path=snapshot.path,
                previous_head=current_head,
                restored_head=snapshot_scan.verification.last_valid_hash or GENESIS_HASH,
                restored_sequence=snapshot_scan.verification.last_sequence or 0,
                pre_restore_backup=backup_dir,
            )
