"""Dry-run-first physical purge through verified ledger rewrite.

The controller rewrites the active ledger only after an explicit plan, expected-head
check, verified pre-purge snapshot, semantic verification, and confirmation token.
It never claims deletion from offline or user-controlled backups.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..domain.ledger import GENESIS_HASH, LedgerEvent, LedgerTransaction, canonical_json
from ..infrastructure.jsonl_ledger import JsonlRecordStore
from ..ports.record_store import HeadMismatchError, LedgerIntegrityError, RecordStore
from ..runtime_format import (
    LEDGER_V1_RUNTIME_FORMAT,
    RuntimeFormatManifest,
    load_runtime_format_manifest,
)
from .projection_service import ProjectionCoordinator
from .replay import ReplayState, replay_transactions

_PURGE_PLAN_SCHEMA = "ms8.physical-purge-plan.v1"
_PURGE_REPORT_SCHEMA = "ms8.physical-purge-report.v1"
_SAFE_TOKEN_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")


class PhysicalPurgeError(RuntimeError):
    """Raised when a physical-purge invariant is violated."""


@dataclass(frozen=True, slots=True)
class PurgeResidualLocation:
    path: str
    kind: str
    managed: bool
    note: str

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "kind": self.kind,
            "managed": self.managed,
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class PhysicalPurgePlan:
    schema: str
    purge_id: str
    source_ledger_head: str
    source_last_sequence: int
    source_transaction_count: int
    requested_claim_ids: tuple[str, ...]
    expanded_claim_ids: tuple[str, ...]
    affected_event_ids: tuple[str, ...]
    affected_evidence_ids: tuple[str, ...]
    affected_decision_ids: tuple[str, ...]
    affected_conflict_ids: tuple[str, ...]
    affected_sequences: tuple[int, ...]
    rewritten_transaction_count: int
    dropped_transaction_count: int
    target_ledger_head: str
    target_last_sequence: int
    retained_claim_count: int
    content_hash: str
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "purge_id": self.purge_id,
            "source_ledger_head": self.source_ledger_head,
            "source_last_sequence": self.source_last_sequence,
            "source_transaction_count": self.source_transaction_count,
            "requested_claim_ids": list(self.requested_claim_ids),
            "expanded_claim_ids": list(self.expanded_claim_ids),
            "affected_event_ids": list(self.affected_event_ids),
            "affected_evidence_ids": list(self.affected_evidence_ids),
            "affected_decision_ids": list(self.affected_decision_ids),
            "affected_conflict_ids": list(self.affected_conflict_ids),
            "affected_sequences": list(self.affected_sequences),
            "rewritten_transaction_count": self.rewritten_transaction_count,
            "dropped_transaction_count": self.dropped_transaction_count,
            "target_ledger_head": self.target_ledger_head,
            "target_last_sequence": self.target_last_sequence,
            "retained_claim_count": self.retained_claim_count,
            "content_hash": self.content_hash,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class PhysicalPurgeResult:
    applied: bool
    dry_run: bool
    purge_id: str
    source_ledger_head: str
    target_ledger_head: str
    purged_claim_ids: tuple[str, ...]
    backup_path: Path | None
    report_path: Path | None
    residual_locations: tuple[PurgeResidualLocation, ...]


def _require_token(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text or any(character not in _SAFE_TOKEN_CHARS for character in text):
        raise PhysicalPurgeError(
            f"{field_name} must use letters, numbers, dot, dash, or underscore"
        )
    return text


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        return
    finally:
        os.close(descriptor)


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _event_identity(event: LedgerEvent) -> tuple[str, str]:
    payload = event.payload
    if event.type == "memory_event.recorded":
        return "event", str(payload.get("event_id") or "")
    if event.type == "claim.proposed":
        return "claim", str(payload.get("claim_id") or "")
    if event.type == "evidence.linked":
        return "evidence", str(payload.get("evidence_id") or "")
    if event.type == "decision.made":
        return "decision", str(payload.get("decision_id") or "")
    if event.type == "conflict.detected":
        return "conflict", str(payload.get("conflict_id") or "")
    raise PhysicalPurgeError(f"unsupported purge event type: {event.type}")


def _decision_claim_ids(payload: Mapping[str, Any]) -> set[str]:
    identifiers: set[str] = set()
    raw_targets = payload.get("target_claim_ids", [])
    if isinstance(raw_targets, (list, tuple)):
        identifiers.update(str(value) for value in raw_targets if str(value))
    result_claim_id = str(payload.get("result_claim_id") or "")
    if result_claim_id:
        identifiers.add(result_claim_id)
    return identifiers


def _conflict_claim_ids(payload: Mapping[str, Any]) -> set[str]:
    raw = payload.get("claim_ids", [])
    if not isinstance(raw, (list, tuple)):
        return set()
    return {str(value) for value in raw if str(value)}


def _expanded_purge_scope(state: ReplayState, requested_claim_ids: set[str]) -> set[str]:
    missing = sorted(requested_claim_ids.difference(state.claims))
    if missing:
        raise PhysicalPurgeError("unknown claim IDs: " + ",".join(missing))

    expanded = set(requested_claim_ids)
    changed = True
    while changed:
        changed = False
        event_ids = {
            state.claims[claim_id].claim.created_from_event_id
            for claim_id in expanded
        }
        event_ids.update(
            evidence.event_id
            for evidence in state.evidence.values()
            if evidence.claim_id in expanded
        )
        for claim_id, view in state.claims.items():
            if claim_id in expanded:
                continue
            if view.claim.created_from_event_id in event_ids:
                expanded.add(claim_id)
                changed = True
                continue
            if any(
                evidence.claim_id == claim_id and evidence.event_id in event_ids
                for evidence in state.evidence.values()
            ):
                expanded.add(claim_id)
                changed = True
        for decision in state.decisions.values():
            referenced = set(decision.target_claim_ids)
            if decision.result_claim_id:
                referenced.add(decision.result_claim_id)
            if referenced.intersection(expanded):
                before = len(expanded)
                expanded.update(referenced)
                changed = changed or len(expanded) != before
    return expanded


def _affected_ids(
    state: ReplayState,
    expanded_claim_ids: set[str],
) -> tuple[set[str], set[str], set[str], set[str]]:
    event_ids = {
        state.claims[claim_id].claim.created_from_event_id
        for claim_id in expanded_claim_ids
    }
    evidence_ids: set[str] = set()
    for evidence_id, evidence in state.evidence.items():
        if evidence.claim_id in expanded_claim_ids or evidence.event_id in event_ids:
            evidence_ids.add(evidence_id)
            event_ids.add(evidence.event_id)

    decision_ids = {
        decision_id
        for decision_id, decision in state.decisions.items()
        if set(decision.target_claim_ids).intersection(expanded_claim_ids)
        or (decision.result_claim_id in expanded_claim_ids if decision.result_claim_id else False)
    }
    conflict_ids = {
        conflict_id
        for conflict_id, conflict in state.conflicts.items()
        if _conflict_claim_ids(conflict).intersection(expanded_claim_ids)
    }
    return event_ids, evidence_ids, decision_ids, conflict_ids


def _should_remove_event(
    event: LedgerEvent,
    *,
    claim_ids: set[str],
    event_ids: set[str],
    evidence_ids: set[str],
    decision_ids: set[str],
    conflict_ids: set[str],
) -> bool:
    kind, identifier = _event_identity(event)
    if kind == "claim":
        return identifier in claim_ids
    if kind == "event":
        return identifier in event_ids
    if kind == "evidence":
        return identifier in evidence_ids
    if kind == "decision":
        return identifier in decision_ids
    return identifier in conflict_ids


def _rewrite_transactions(
    transactions: tuple[LedgerTransaction, ...],
    *,
    purge_id: str,
    claim_ids: set[str],
    event_ids: set[str],
    evidence_ids: set[str],
    decision_ids: set[str],
    conflict_ids: set[str],
) -> tuple[tuple[LedgerTransaction, ...], tuple[int, ...], int]:
    rewritten: list[LedgerTransaction] = []
    affected_sequences: list[int] = []
    dropped = 0
    prev_hash = GENESIS_HASH
    sequence = 1
    for transaction in transactions:
        retained = tuple(
            event
            for event in transaction.events
            if not _should_remove_event(
                event,
                claim_ids=claim_ids,
                event_ids=event_ids,
                evidence_ids=evidence_ids,
                decision_ids=decision_ids,
                conflict_ids=conflict_ids,
            )
        )
        if len(retained) != len(transaction.events):
            affected_sequences.append(transaction.sequence)
        if not retained:
            dropped += 1
            continue
        material = canonical_json(
            {
                "purge_id": purge_id,
                "source_transaction_id": transaction.transaction_id,
                "events": [event.to_dict() for event in retained],
            }
        )
        transaction_id = "txn_purge_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
        rewritten_transaction = LedgerTransaction.create(
            sequence=sequence,
            prev_hash=prev_hash,
            actor=transaction.actor,
            events=retained,
            transaction_id=transaction_id,
            recorded_at=transaction.recorded_at,
        )
        rewritten.append(rewritten_transaction)
        prev_hash = rewritten_transaction.hash
        sequence += 1
    return tuple(rewritten), tuple(affected_sequences), dropped


def _plan_hash(payload: Mapping[str, Any]) -> str:
    material = {key: value for key, value in payload.items() if key != "content_hash"}
    return _sha256_bytes(canonical_json(material).encode("utf-8"))


class PhysicalPurgeController:
    """Plan and apply a verified physical purge without touching unknown backups."""

    def __init__(
        self,
        *,
        runtime_manifest_path: Path,
        record_store: RecordStore,
        projection_coordinator: ProjectionCoordinator,
        backup_root: Path,
        staging_root: Path,
        additional_backup_roots: Iterable[Path] = (),
    ) -> None:
        self.runtime_manifest_path = Path(runtime_manifest_path)
        self.record_store = record_store
        self.projection_coordinator = projection_coordinator
        self.backup_root = Path(backup_root)
        self.staging_root = Path(staging_root)
        self.additional_backup_roots = tuple(Path(path) for path in additional_backup_roots)

    def _verified_runtime(self) -> tuple[RuntimeFormatManifest, tuple[LedgerTransaction, ...], ReplayState]:
        manifest = load_runtime_format_manifest(self.runtime_manifest_path)
        if manifest.active_format != LEDGER_V1_RUNTIME_FORMAT:
            raise PhysicalPurgeError("physical purge requires ledger-v1 runtime authority")
        verification = self.record_store.verify()
        if not verification.valid:
            raise LedgerIntegrityError("cannot purge an invalid ledger")
        current_head = verification.last_valid_hash or GENESIS_HASH
        if manifest.ledger_head != current_head:
            raise PhysicalPurgeError("runtime-format manifest ledger head does not match authority")
        transactions = tuple(self.record_store.iterate())
        state = replay_transactions(transactions)
        if state.ledger_head != current_head:
            raise PhysicalPurgeError("replay head does not match authoritative ledger")
        return manifest, transactions, state

    def plan(self, claim_ids: Iterable[str], *, purge_id: str) -> PhysicalPurgePlan:
        normalized_purge_id = _require_token(purge_id, "purge_id")
        requested = {_require_token(value, "claim_id") for value in claim_ids}
        if not requested:
            raise PhysicalPurgeError("at least one claim ID is required")
        _manifest, transactions, state = self._verified_runtime()
        expanded = _expanded_purge_scope(state, requested)
        event_ids, evidence_ids, decision_ids, conflict_ids = _affected_ids(state, expanded)
        rewritten, affected_sequences, dropped = _rewrite_transactions(
            transactions,
            purge_id=normalized_purge_id,
            claim_ids=expanded,
            event_ids=event_ids,
            evidence_ids=evidence_ids,
            decision_ids=decision_ids,
            conflict_ids=conflict_ids,
        )
        rewritten_state = replay_transactions(rewritten)
        surviving_claims = set(state.claims).difference(expanded)
        if set(rewritten_state.claims) != surviving_claims:
            raise PhysicalPurgeError("rewritten ledger changed the surviving claim set")
        for claim_id in surviving_claims:
            if rewritten_state.claims[claim_id].current_status != state.claims[claim_id].current_status:
                raise PhysicalPurgeError(f"rewritten ledger changed surviving claim status: {claim_id}")

        warnings = (
            "The active ledger and rebuilt projections can be purged, but managed pre-purge snapshots retain the removed data for rollback.",
            "Deletion from offline, exported, remote, or user-controlled backups cannot be guaranteed or claimed.",
        )
        payload: dict[str, object] = {
            "schema": _PURGE_PLAN_SCHEMA,
            "purge_id": normalized_purge_id,
            "source_ledger_head": state.ledger_head,
            "source_last_sequence": state.last_sequence,
            "source_transaction_count": len(transactions),
            "requested_claim_ids": sorted(requested),
            "expanded_claim_ids": sorted(expanded),
            "affected_event_ids": sorted(event_ids),
            "affected_evidence_ids": sorted(evidence_ids),
            "affected_decision_ids": sorted(decision_ids),
            "affected_conflict_ids": sorted(conflict_ids),
            "affected_sequences": list(affected_sequences),
            "rewritten_transaction_count": len(rewritten),
            "dropped_transaction_count": dropped,
            "target_ledger_head": rewritten_state.ledger_head,
            "target_last_sequence": rewritten_state.last_sequence,
            "retained_claim_count": len(rewritten_state.claims),
            "warnings": list(warnings),
        }
        content_hash = _plan_hash(payload)
        return PhysicalPurgePlan(
            schema=_PURGE_PLAN_SCHEMA,
            purge_id=normalized_purge_id,
            source_ledger_head=state.ledger_head,
            source_last_sequence=state.last_sequence,
            source_transaction_count=len(transactions),
            requested_claim_ids=tuple(sorted(requested)),
            expanded_claim_ids=tuple(sorted(expanded)),
            affected_event_ids=tuple(sorted(event_ids)),
            affected_evidence_ids=tuple(sorted(evidence_ids)),
            affected_decision_ids=tuple(sorted(decision_ids)),
            affected_conflict_ids=tuple(sorted(conflict_ids)),
            affected_sequences=affected_sequences,
            rewritten_transaction_count=len(rewritten),
            dropped_transaction_count=dropped,
            target_ledger_head=rewritten_state.ledger_head,
            target_last_sequence=rewritten_state.last_sequence,
            retained_claim_count=len(rewritten_state.claims),
            content_hash=content_hash,
            warnings=warnings,
        )

    def _assert_plan_current(self, plan: PhysicalPurgePlan) -> tuple[RuntimeFormatManifest, tuple[LedgerTransaction, ...]]:
        current = self.plan(plan.requested_claim_ids, purge_id=plan.purge_id)
        if current.content_hash != plan.content_hash or current.source_ledger_head != plan.source_ledger_head:
            raise PhysicalPurgeError("physical purge plan is stale or was modified")
        manifest, transactions, _state = self._verified_runtime()
        return manifest, transactions

    def _residual_locations(self, backup_path: Path) -> tuple[PurgeResidualLocation, ...]:
        locations = [
            PurgeResidualLocation(
                path=str(backup_path),
                kind="managed_pre_purge_snapshot",
                managed=True,
                note="Retained for rollback; still contains physically purged records.",
            )
        ]
        locations.extend(
            PurgeResidualLocation(
                path=str(path),
                kind="declared_additional_backup_root",
                managed=False,
                note="Presence is reported only; this controller does not delete or rewrite it.",
            )
            for path in self.additional_backup_roots
        )
        locations.append(
            PurgeResidualLocation(
                path="unbounded",
                kind="offline_or_user_controlled_backups",
                managed=False,
                note="Deletion cannot be verified or claimed.",
            )
        )
        return tuple(locations)

    def _write_runtime_manifest(
        self,
        target: RuntimeFormatManifest,
        *,
        expected: RuntimeFormatManifest,
    ) -> None:
        current = load_runtime_format_manifest(self.runtime_manifest_path)
        if current != expected:
            raise PhysicalPurgeError("runtime-format manifest changed during purge")
        _atomic_write(
            self.runtime_manifest_path,
            (canonical_json(target.to_dict()) + "\n").encode("utf-8"),
        )
        if load_runtime_format_manifest(self.runtime_manifest_path) != target:
            raise PhysicalPurgeError("runtime-format manifest purge update failed verification")

    def apply(
        self,
        plan: PhysicalPurgePlan,
        *,
        expected_head: str,
        updated_at: str,
        confirmation: str | None = None,
        dry_run: bool = True,
    ) -> PhysicalPurgeResult:
        manifest, transactions = self._assert_plan_current(plan)
        if expected_head != plan.source_ledger_head:
            raise HeadMismatchError(
                f"expected head {expected_head}, current head {plan.source_ledger_head}"
            )
        if dry_run:
            return PhysicalPurgeResult(
                applied=False,
                dry_run=True,
                purge_id=plan.purge_id,
                source_ledger_head=plan.source_ledger_head,
                target_ledger_head=plan.target_ledger_head,
                purged_claim_ids=plan.expanded_claim_ids,
                backup_path=None,
                report_path=None,
                residual_locations=(),
            )
        if confirmation != plan.purge_id:
            raise PhysicalPurgeError("physical purge confirmation must exactly match purge_id")

        purge_root = self.backup_root / plan.purge_id
        staging_path = self.staging_root / plan.purge_id
        if purge_root.exists() or staging_path.exists():
            raise FileExistsError("purge backup or staging path already exists")
        purge_root.mkdir(parents=True, exist_ok=False)
        staging_path.parent.mkdir(parents=True, exist_ok=True)

        manifest_present = self.runtime_manifest_path.is_file()
        manifest_bytes = self.runtime_manifest_path.read_bytes() if manifest_present else None
        backup_snapshot = self.record_store.snapshot()
        backup_path = purge_root / "ledger-snapshot"
        exported = self.record_store.export_snapshot(backup_snapshot, backup_path)
        _atomic_write(
            purge_root / "purge-plan.json",
            (canonical_json(plan.to_dict()) + "\n").encode("utf-8"),
        )

        expanded = set(plan.expanded_claim_ids)
        event_ids = set(plan.affected_event_ids)
        evidence_ids = set(plan.affected_evidence_ids)
        decision_ids = set(plan.affected_decision_ids)
        conflict_ids = set(plan.affected_conflict_ids)
        rewritten, _affected_sequences, _dropped = _rewrite_transactions(
            transactions,
            purge_id=plan.purge_id,
            claim_ids=expanded,
            event_ids=event_ids,
            evidence_ids=evidence_ids,
            decision_ids=decision_ids,
            conflict_ids=conflict_ids,
        )

        staging_store = JsonlRecordStore(staging_path)
        staging_head = GENESIS_HASH
        for transaction in rewritten:
            result = staging_store.append(transaction, expected_head=staging_head)
            staging_head = result.new_head
        staging_verification = staging_store.verify()
        if not staging_verification.valid or staging_head != plan.target_ledger_head:
            raise PhysicalPurgeError("staged purge ledger failed verification")
        staging_state = replay_transactions(staging_store.iterate())
        if set(staging_state.claims).intersection(expanded):
            raise PhysicalPurgeError("staged purge ledger still contains purged claims")
        staging_snapshot = staging_store.snapshot()

        target_manifest = RuntimeFormatManifest(
            schema=manifest.schema,
            active_format=manifest.active_format,
            generation=manifest.generation + 1,
            updated_at=updated_at,
            previous_format=manifest.previous_format,
            migration_id=manifest.migration_id,
            ledger_head=plan.target_ledger_head,
        )
        residuals = self._residual_locations(purge_root)
        report_path = purge_root / "purge-report.json"
        try:
            self.record_store.restore_snapshot(
                staging_snapshot.path,
                expected_head=plan.source_ledger_head,
                dry_run=False,
            )
            rebuilt = self.projection_coordinator.rebuild_all()
            status = self.projection_coordinator.require_ready_for_query()
            if rebuilt.ledger_head != plan.target_ledger_head or status.ledger_head != plan.target_ledger_head:
                raise PhysicalPurgeError("projection rebuild does not match purged ledger head")
            self._write_runtime_manifest(target_manifest, expected=manifest)
            report = {
                "schema": _PURGE_REPORT_SCHEMA,
                "purge_id": plan.purge_id,
                "applied": True,
                "source_ledger_head": plan.source_ledger_head,
                "target_ledger_head": plan.target_ledger_head,
                "purged_claim_ids": list(plan.expanded_claim_ids),
                "backup_snapshot_head": exported.ledger_head,
                "projection_names": [item.descriptor.name for item in rebuilt.projections],
                "residual_locations": [item.to_dict() for item in residuals],
                "deletion_scope": "active ledger and rebuilt projections only",
                "offline_backup_deletion_claimed": False,
            }
            _atomic_write(report_path, (canonical_json(report) + "\n").encode("utf-8"))
        except (OSError, RuntimeError, TypeError, ValueError):
            current_head = self.record_store.verify().last_valid_hash or GENESIS_HASH
            self.record_store.restore_snapshot(
                exported.path,
                expected_head=current_head,
                dry_run=False,
            )
            self.projection_coordinator.rebuild_all()
            if manifest_bytes is None:
                self.runtime_manifest_path.unlink(missing_ok=True)
                _fsync_directory(self.runtime_manifest_path.parent)
            else:
                _atomic_write(self.runtime_manifest_path, manifest_bytes)
            raise
        finally:
            shutil.rmtree(staging_path, ignore_errors=True)

        return PhysicalPurgeResult(
            applied=True,
            dry_run=False,
            purge_id=plan.purge_id,
            source_ledger_head=plan.source_ledger_head,
            target_ledger_head=plan.target_ledger_head,
            purged_claim_ids=plan.expanded_claim_ids,
            backup_path=purge_root,
            report_path=report_path,
            residual_locations=residuals,
        )


__all__ = [
    "PhysicalPurgeController",
    "PhysicalPurgeError",
    "PhysicalPurgePlan",
    "PhysicalPurgeResult",
    "PurgeResidualLocation",
]
