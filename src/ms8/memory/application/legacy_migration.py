"""Dry-run-first migration from legacy canonical records into ledger-v1.

This module deliberately targets an empty staging RecordStore. It does not read
production paths, change the runtime-format manifest, or enable ledger-v1. A
caller must explicitly supply legacy rows and an isolated target store.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..domain.ledger import GENESIS_HASH, LedgerEvent, LedgerTransaction, canonical_json
from ..domain.models import Actor, Claim, Decision, Evidence, MemoryEvent, ValidTime
from ..ports.record_store import LedgerIntegrityError, RecordStore
from .projection_service import ProjectionCoordinator

_MIGRATION_SCHEMA = "ms8.legacy-migration-plan.v1"
_KNOWN_LEGACY_FIELDS = {
    "id",
    "text",
    "normalized_text",
    "category",
    "status",
    "source",
    "created_at",
    "meta",
    "scope",
    "authority",
    "sensitivity",
    "can_recall",
    "can_inject",
    "can_act_on",
}
_STATUS_MAP = {
    "candidate": "proposed",
    "short_term": "pending_review",
    "accepted": "accepted",
    "verified": "verified",
    "pending_review": "pending_review",
    "quarantined": "disputed",
    "stale": "expired",
    "superseded": "superseded",
    "revoked": "revoked",
}
_KIND_MAP = {
    "user_preference": "preference",
    "product_decision": "decision",
    "system_diagnostic": "summary",
    "experimental_note": "fact",
    "general": "fact",
}


class LegacyMigrationError(ValueError):
    """Raised when a staged migration violates migration invariants."""


@dataclass(frozen=True, slots=True)
class MigrationIssue:
    record_index: int
    code: str
    detail: str

    def to_dict(self) -> dict[str, object]:
        return {
            "record_index": self.record_index,
            "code": self.code,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class MigrationRecordPreview:
    record_index: int
    legacy_id: str
    event_id: str
    claim_id: str
    evidence_id: str
    decision_id: str
    transaction_id: str
    legacy_status: str
    mapped_status: str
    preserved_unknown_fields: tuple[str, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "record_index": self.record_index,
            "legacy_id": self.legacy_id,
            "event_id": self.event_id,
            "claim_id": self.claim_id,
            "evidence_id": self.evidence_id,
            "decision_id": self.decision_id,
            "transaction_id": self.transaction_id,
            "legacy_status": self.legacy_status,
            "mapped_status": self.mapped_status,
            "preserved_unknown_fields": list(self.preserved_unknown_fields),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    schema: str
    migration_id: str
    recorded_at: str
    source_count: int
    migratable_count: int
    rejected_count: int
    previews: tuple[MigrationRecordPreview, ...]
    issues: tuple[MigrationIssue, ...]
    content_hash: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "migration_id": self.migration_id,
            "recorded_at": self.recorded_at,
            "source_count": self.source_count,
            "migratable_count": self.migratable_count,
            "rejected_count": self.rejected_count,
            "previews": [item.to_dict() for item in self.previews],
            "issues": [item.to_dict() for item in self.issues],
            "content_hash": self.content_hash,
        }


@dataclass(frozen=True, slots=True)
class PreparedMigration:
    plan: MigrationPlan
    transactions: tuple[LedgerTransaction, ...]


@dataclass(frozen=True, slots=True)
class MigrationApplyResult:
    migration_id: str
    applied_transactions: int
    ledger_head: str
    last_sequence: int
    logical_state_hash: str | None
    projection_names: tuple[str, ...]


def _sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stable_token(prefix: str, record_index: int, row_hash: str) -> str:
    material = f"{record_index}:{row_hash}"
    return prefix + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def _valid_timestamp(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    return text if parsed.tzinfo is not None else None


def _mapped_status(value: object) -> tuple[str, str | None]:
    legacy_status = str(value or "candidate").strip().lower() or "candidate"
    mapped = _STATUS_MAP.get(legacy_status)
    if mapped is None:
        return "proposed", f"unsupported legacy status {legacy_status!r} mapped to proposed"
    return mapped, None


def _claim_kind(value: object) -> tuple[str, str | None]:
    category = str(value or "general").strip().lower() or "general"
    kind = _KIND_MAP.get(category)
    if kind is None:
        return "fact", f"unknown legacy category {category!r} mapped to fact"
    return kind, None


def _confidence(row: Mapping[str, Any]) -> float:
    meta = row.get("meta")
    raw = meta.get("confidence") if isinstance(meta, Mapping) else None
    if raw is None:
        return 0.75
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.75
    return value if 0.0 <= value <= 1.0 else 0.75


def _trust_class(authority: str, source: str) -> str:
    normalized_authority = authority.strip().lower()
    normalized_source = source.strip().lower()
    if normalized_authority == "user_explicit" or normalized_source in {"ask", "user", "manual"}:
        return "user_explicit"
    if normalized_authority in {"user_implicit", "assistant_inferred"}:
        return "user_implicit"
    if normalized_authority == "system_observed":
        return "system_observed"
    return "tool_generated"


def _unknown_fields(row: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): value for key, value in row.items() if str(key) not in _KNOWN_LEGACY_FIELDS}


def _prepare_record(
    row: Mapping[str, Any],
    *,
    record_index: int,
    migration_id: str,
    recorded_at: str,
    sequence: int,
    prev_hash: str,
) -> tuple[MigrationRecordPreview, LedgerTransaction]:
    raw_text = str(row.get("normalized_text") or row.get("text") or "").strip()
    if not raw_text:
        raise LegacyMigrationError("legacy record has no text or normalized_text")
    serialized = canonical_json(dict(row))
    row_hash = _sha256_text(serialized)
    legacy_id = str(row.get("id") or f"legacy_row_{record_index}").strip()
    if not legacy_id:
        legacy_id = f"legacy_row_{record_index}"
    legacy_status = str(row.get("status") or "candidate").strip().lower() or "candidate"
    mapped_status, status_warning = _mapped_status(legacy_status)
    claim_kind, category_warning = _claim_kind(row.get("category"))
    warnings = tuple(value for value in (status_warning, category_warning) if value is not None)
    observed_at = _valid_timestamp(row.get("created_at")) or recorded_at
    observed_precision = "exact" if _valid_timestamp(row.get("created_at")) else "legacy_inferred"
    source = str(row.get("source") or "unknown").strip() or "unknown"
    scope = str(row.get("scope") or "personal").strip() or "personal"
    authority = str(row.get("authority") or "system_observed").strip() or "system_observed"
    sensitivity = str(row.get("sensitivity") or "private").strip() or "private"
    category = str(row.get("category") or "general").strip() or "general"
    realm_id = "legacy:default"
    meta = row.get("meta")
    if isinstance(meta, Mapping):
        candidate_realm = str(meta.get("workspace_realm_id") or meta.get("realm_id") or "").strip()
        if candidate_realm:
            realm_id = candidate_realm
    preserved = _unknown_fields(row)

    event_id = _stable_token("evt_legacy_", record_index, row_hash)
    claim_id = _stable_token("clm_legacy_", record_index, row_hash)
    evidence_id = _stable_token("evd_legacy_", record_index, row_hash)
    decision_id = _stable_token("dec_legacy_", record_index, row_hash)
    transaction_id = _stable_token("txn_legacy_", record_index, row_hash)

    memory_event = MemoryEvent(
        event_id=event_id,
        kind="legacy_import",
        content={
            "text": raw_text,
            "normalized_text": str(row.get("normalized_text") or raw_text),
            "category": category,
            "record_hash": row_hash,
            "legacy_meta": preserved,
        },
        source={
            "system": "legacy_canonical_records",
            "legacy_id": legacy_id,
            "legacy_source": source,
            "migration_id": migration_id,
        },
        observed_at=observed_at,
        observed_at_precision=observed_precision,
        trust_class=_trust_class(authority, source),
    )
    claim = Claim(
        claim_id=claim_id,
        kind=claim_kind,
        text=raw_text,
        subject=f"legacy:{legacy_id}",
        predicate=category,
        value=raw_text,
        scope=scope,
        realm_id=realm_id,
        authority=authority,
        sensitivity=sensitivity,
        confidence=_confidence(row),
        status="proposed",
        valid_time=ValidTime(
            start=_valid_timestamp(row.get("created_at")),
            basis="legacy_inferred",
        ),
        created_from_event_id=event_id,
    )
    evidence = Evidence(
        evidence_id=evidence_id,
        claim_id=claim_id,
        event_id=event_id,
        relation="supports",
        fragment={
            "legacy_record_index": record_index,
            "legacy_record_hash": row_hash,
        },
        quoted_text_hash=_sha256_text(raw_text),
    )
    decision = Decision(
        decision_id=decision_id,
        action="admit",
        result_claim_id=claim_id,
        result_status=mapped_status,
        policy={
            "engine_version": "legacy-migration-v1",
            "migration_id": migration_id,
            "legacy_status": legacy_status,
            "legacy_meta": preserved,
            "governance": {
                "can_recall": bool(row.get("can_recall", True)),
                "can_inject": bool(row.get("can_inject", False)),
                "can_act_on": bool(row.get("can_act_on", False)),
            },
            "warnings": list(warnings),
        },
        actor=Actor(kind="migration", id=migration_id),
        reason=f"Imported legacy canonical record {legacy_id}",
        recorded_at=recorded_at,
    )
    transaction = LedgerTransaction.create(
        sequence=sequence,
        prev_hash=prev_hash,
        actor=Actor(kind="migration", id=migration_id),
        recorded_at=recorded_at,
        transaction_id=transaction_id,
        events=(
            LedgerEvent(type="memory_event.recorded", payload=memory_event.to_dict()),
            LedgerEvent(type="claim.proposed", payload=claim.to_dict()),
            LedgerEvent(type="evidence.linked", payload=evidence.to_dict()),
            LedgerEvent(type="decision.made", payload=decision.to_dict()),
        ),
    )
    preview = MigrationRecordPreview(
        record_index=record_index,
        legacy_id=legacy_id,
        event_id=event_id,
        claim_id=claim_id,
        evidence_id=evidence_id,
        decision_id=decision_id,
        transaction_id=transaction_id,
        legacy_status=legacy_status,
        mapped_status=mapped_status,
        preserved_unknown_fields=tuple(sorted(preserved)),
        warnings=warnings,
    )
    return preview, transaction


def prepare_legacy_migration(
    records: Iterable[object],
    *,
    migration_id: str,
    recorded_at: str,
) -> PreparedMigration:
    """Build a deterministic dry-run plan and transactions without writing files."""

    normalized_migration_id = str(migration_id or "").strip()
    if not normalized_migration_id:
        raise LegacyMigrationError("migration_id is required")
    if _valid_timestamp(recorded_at) is None:
        raise LegacyMigrationError("recorded_at must be timezone-aware ISO-8601")

    rows = tuple(records)
    previews: list[MigrationRecordPreview] = []
    issues: list[MigrationIssue] = []
    transactions: list[LedgerTransaction] = []
    prev_hash = GENESIS_HASH
    sequence = 1
    for index, raw in enumerate(rows, start=1):
        if not isinstance(raw, Mapping):
            issues.append(MigrationIssue(index, "invalid_record_type", type(raw).__name__))
            continue
        try:
            preview, transaction = _prepare_record(
                raw,
                record_index=index,
                migration_id=normalized_migration_id,
                recorded_at=recorded_at,
                sequence=sequence,
                prev_hash=prev_hash,
            )
        except (LegacyMigrationError, TypeError, ValueError) as exc:
            issues.append(MigrationIssue(index, "record_rejected", str(exc)))
            continue
        previews.append(preview)
        transactions.append(transaction)
        prev_hash = transaction.hash
        sequence += 1

    plan_material = {
        "schema": _MIGRATION_SCHEMA,
        "migration_id": normalized_migration_id,
        "recorded_at": recorded_at,
        "source_count": len(rows),
        "previews": [item.to_dict() for item in previews],
        "issues": [item.to_dict() for item in issues],
        "transaction_hashes": [item.hash for item in transactions],
    }
    content_hash = _sha256_text(canonical_json(plan_material))
    plan = MigrationPlan(
        schema=_MIGRATION_SCHEMA,
        migration_id=normalized_migration_id,
        recorded_at=recorded_at,
        source_count=len(rows),
        migratable_count=len(transactions),
        rejected_count=len(issues),
        previews=tuple(previews),
        issues=tuple(issues),
        content_hash=content_hash,
    )
    return PreparedMigration(plan=plan, transactions=tuple(transactions))


class LegacyMigrationStagingService:
    """Apply a prepared migration only to an empty staging ledger."""

    def __init__(
        self,
        record_store: RecordStore,
        projection_coordinator: ProjectionCoordinator | None = None,
    ):
        self.record_store = record_store
        self.projection_coordinator = projection_coordinator

    def apply(self, prepared: PreparedMigration) -> MigrationApplyResult:
        verification = self.record_store.verify()
        if not verification.valid:
            raise LedgerIntegrityError(
                "cannot stage migration into invalid ledger: "
                + ",".join(verification.reason_codes)
            )
        current_head = verification.last_valid_hash or GENESIS_HASH
        if verification.transaction_count != 0 or current_head != GENESIS_HASH:
            raise LegacyMigrationError("staging ledger must be empty")
        if prepared.plan.rejected_count:
            raise LegacyMigrationError("migration plan contains rejected records")

        expected_head = GENESIS_HASH
        for transaction in prepared.transactions:
            result = self.record_store.append(transaction, expected_head=expected_head)
            expected_head = result.new_head

        final = self.record_store.verify()
        if not final.valid:
            raise LedgerIntegrityError(
                "staged ledger failed verification: " + ",".join(final.reason_codes)
            )
        if final.transaction_count != prepared.plan.migratable_count:
            raise LedgerIntegrityError("staged transaction count does not match migration plan")

        logical_state_hash: str | None = None
        projection_names: tuple[str, ...] = ()
        if self.projection_coordinator is not None:
            built = self.projection_coordinator.rebuild_all()
            logical_state_hash = built.logical_state_hash
            projection_names = tuple(item.descriptor.name for item in built.projections)

        return MigrationApplyResult(
            migration_id=prepared.plan.migration_id,
            applied_transactions=final.transaction_count,
            ledger_head=final.last_valid_hash or GENESIS_HASH,
            last_sequence=final.last_sequence or 0,
            logical_state_hash=logical_state_hash,
            projection_names=projection_names,
        )


__all__ = [
    "LegacyMigrationError",
    "LegacyMigrationStagingService",
    "MigrationApplyResult",
    "MigrationIssue",
    "MigrationPlan",
    "MigrationRecordPreview",
    "PreparedMigration",
    "prepare_legacy_migration",
]
