"""SQLite read-model projection rebuilt from the authoritative ledger."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ..application.replay import ClaimReplayView, ReplayState, replay_transactions
from ..application.temporal_query import effective_valid_until
from ..domain.ledger import GENESIS_HASH, canonical_json
from ..ports.record_store import LedgerIntegrityError, RecordStore
from .durable_io import replace_path

PROJECTION_SCHEMA = "ms8.sqlite_projection.v2"
BUILDER_VERSION = "2"
_RECALLABLE_STATUSES = (
    "proposed",
    "pending_review",
    "accepted",
    "verified",
    "disputed",
)


@dataclass(frozen=True, slots=True)
class ProjectionManifest:
    projection_schema: str
    built_from_ledger_head: str
    last_sequence: int
    built_at: str
    builder_version: str
    logical_state_hash: str
    memory_event_count: int
    claim_count: int
    evidence_count: int
    decision_count: int
    conflict_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "projection_schema": self.projection_schema,
            "built_from_ledger_head": self.built_from_ledger_head,
            "last_sequence": self.last_sequence,
            "built_at": self.built_at,
            "builder_version": self.builder_version,
            "logical_state_hash": self.logical_state_hash,
            "memory_event_count": self.memory_event_count,
            "claim_count": self.claim_count,
            "evidence_count": self.evidence_count,
            "decision_count": self.decision_count,
            "conflict_count": self.conflict_count,
        }


@dataclass(frozen=True, slots=True)
class ProjectionBuildResult:
    path: Path
    manifest: ProjectionManifest
    replaced_existing: bool


@dataclass(frozen=True, slots=True)
class ProjectionFreshness:
    exists: bool
    fresh: bool
    projection_head: str | None
    ledger_head: str
    reason: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _create_schema(connection: sqlite3.Connection) -> None:
    recallable_statuses = ", ".join(f"'{value}'" for value in _RECALLABLE_STATUSES)
    connection.executescript(
        f"""
        CREATE TABLE projection_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE memory_events (
            event_id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            trust_class TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );
        CREATE TABLE claims (
            claim_id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            subject TEXT NOT NULL,
            predicate TEXT NOT NULL,
            realm_id TEXT NOT NULL,
            scope TEXT NOT NULL,
            authority TEXT NOT NULL,
            sensitivity TEXT NOT NULL,
            confidence REAL NOT NULL,
            proposed_status TEXT NOT NULL,
            current_status TEXT NOT NULL,
            is_forgotten INTEGER NOT NULL CHECK(is_forgotten IN (0, 1)),
            valid_from TEXT,
            valid_until TEXT,
            valid_time_basis TEXT NOT NULL,
            created_from_event_id TEXT NOT NULL,
            text TEXT NOT NULL,
            value_json TEXT NOT NULL,
            decision_ids_json TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            FOREIGN KEY(created_from_event_id) REFERENCES memory_events(event_id)
        );
        CREATE TABLE claim_tombstones (
            claim_id TEXT PRIMARY KEY,
            realm_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            action TEXT NOT NULL,
            FOREIGN KEY(claim_id) REFERENCES claims(claim_id)
        );
        CREATE TABLE evidence (
            evidence_id TEXT PRIMARY KEY,
            claim_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            relation TEXT NOT NULL,
            weight REAL NOT NULL,
            fragment_json TEXT NOT NULL,
            quoted_text_hash TEXT NOT NULL,
            FOREIGN KEY(claim_id) REFERENCES claims(claim_id),
            FOREIGN KEY(event_id) REFERENCES memory_events(event_id)
        );
        CREATE TABLE decisions (
            decision_id TEXT PRIMARY KEY,
            action TEXT NOT NULL,
            result_claim_id TEXT,
            result_status TEXT,
            target_claim_ids_json TEXT NOT NULL,
            actor_json TEXT NOT NULL,
            policy_json TEXT NOT NULL,
            reason TEXT NOT NULL,
            recorded_at TEXT NOT NULL
        );
        CREATE TABLE conflicts (
            conflict_id TEXT PRIMARY KEY,
            claim_ids_json TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );
        CREATE VIEW recallable_claims AS
            SELECT * FROM claims
            WHERE is_forgotten = 0
              AND current_status IN ({recallable_statuses});
        CREATE VIEW recallable_evidence AS
            SELECT evidence.*
            FROM evidence
            JOIN recallable_claims ON recallable_claims.claim_id = evidence.claim_id;
        CREATE VIEW recallable_memory_events AS
            SELECT memory_events.*
            FROM memory_events
            WHERE NOT EXISTS (
                SELECT 1 FROM claims
                WHERE claims.created_from_event_id = memory_events.event_id
            ) OR EXISTS (
                SELECT 1 FROM recallable_claims
                WHERE recallable_claims.created_from_event_id = memory_events.event_id
            );
        CREATE INDEX claims_lookup_idx
            ON claims(realm_id, subject, predicate, current_status, is_forgotten);
        CREATE INDEX evidence_claim_idx ON evidence(claim_id);
        """
    )


def _build_manifest(state: ReplayState) -> ProjectionManifest:
    return ProjectionManifest(
        projection_schema=PROJECTION_SCHEMA,
        built_from_ledger_head=state.ledger_head,
        last_sequence=state.last_sequence,
        built_at=_utc_now(),
        builder_version=BUILDER_VERSION,
        logical_state_hash=state.logical_state_hash,
        memory_event_count=len(state.memory_events),
        claim_count=len(state.claims),
        evidence_count=len(state.evidence),
        decision_count=len(state.decisions),
        conflict_count=len(state.conflicts),
    )


def _latest_action(state: ReplayState, view: ClaimReplayView) -> str | None:
    if not view.decision_ids:
        return None
    decision = state.decisions.get(view.decision_ids[-1])
    return decision.action if decision is not None else None


def _write_state(connection: sqlite3.Connection, state: ReplayState, manifest: ProjectionManifest) -> None:
    for event_id in sorted(state.memory_events):
        event = state.memory_events[event_id]
        connection.execute(
            "INSERT INTO memory_events VALUES (?, ?, ?, ?, ?)",
            (
                event.event_id,
                event.kind,
                event.observed_at,
                event.trust_class,
                canonical_json(event.to_dict()),
            ),
        )
    for claim_id in sorted(state.claims):
        view = state.claims[claim_id]
        claim = view.claim
        latest_action = _latest_action(state, view)
        is_forgotten = latest_action == "forget"
        connection.execute(
            """
            INSERT INTO claims VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                claim.claim_id,
                claim.kind,
                claim.subject,
                claim.predicate,
                claim.realm_id,
                claim.scope,
                claim.authority,
                claim.sensitivity,
                claim.confidence,
                claim.status,
                view.current_status,
                int(is_forgotten),
                claim.valid_time.start,
                effective_valid_until(state, view),
                claim.valid_time.basis,
                claim.created_from_event_id,
                claim.text,
                canonical_json(claim.to_dict()["value"]),
                canonical_json(list(view.decision_ids)),
                canonical_json(claim.to_dict()),
            ),
        )
        if is_forgotten:
            connection.execute(
                "INSERT INTO claim_tombstones VALUES (?, ?, ?, ?)",
                (
                    claim.claim_id,
                    claim.realm_id,
                    view.decision_ids[-1],
                    "forget",
                ),
            )
    for evidence_id in sorted(state.evidence):
        evidence = state.evidence[evidence_id]
        connection.execute(
            "INSERT INTO evidence VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                evidence.evidence_id,
                evidence.claim_id,
                evidence.event_id,
                evidence.relation,
                evidence.weight,
                canonical_json(evidence.to_dict()["fragment"]),
                evidence.quoted_text_hash,
            ),
        )
    for decision_id in sorted(state.decisions):
        decision = state.decisions[decision_id]
        connection.execute(
            "INSERT INTO decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                decision.decision_id,
                decision.action,
                decision.result_claim_id,
                decision.result_status,
                canonical_json(list(decision.target_claim_ids)),
                canonical_json(decision.actor.to_dict()),
                canonical_json(decision.to_dict()["policy"]),
                decision.reason,
                decision.recorded_at,
            ),
        )
    for conflict_id in sorted(state.conflicts):
        conflict = state.conflicts[conflict_id]
        connection.execute(
            "INSERT INTO conflicts VALUES (?, ?, ?)",
            (
                conflict_id,
                canonical_json(list(conflict["claim_ids"])),
                canonical_json(dict(conflict)),
            ),
        )
    for key, value in manifest.to_dict().items():
        connection.execute(
            "INSERT INTO projection_meta(key, value) VALUES (?, ?)",
            (key, canonical_json(value)),
        )


class SQLiteProjectionBuilder:
    """Build and inspect a disposable SQLite projection."""

    def __init__(self, record_store: RecordStore, projection_path: Path):
        self.record_store = record_store
        self.projection_path = Path(projection_path)

    def rebuild(self) -> ProjectionBuildResult:
        verification = self.record_store.verify()
        if not verification.valid:
            raise LedgerIntegrityError("cannot project invalid ledger: " + ",".join(verification.reason_codes))
        state = replay_transactions(self.record_store.iterate())
        manifest = _build_manifest(state)
        self.projection_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.projection_path.with_name(f".{self.projection_path.name}.{uuid4().hex}.tmp")
        replaced_existing = self.projection_path.exists()
        try:
            connection = _connect(temporary)
            try:
                with connection:
                    _create_schema(connection)
                    _write_state(connection, state, manifest)
                connection.execute("PRAGMA wal_checkpoint(FULL)")
            finally:
                connection.close()
            with temporary.open("r+b") as handle:
                os.fsync(handle.fileno())
            replace_path(temporary, self.projection_path)
        finally:
            temporary.unlink(missing_ok=True)
        return ProjectionBuildResult(self.projection_path, manifest, replaced_existing)

    def read_manifest(self) -> ProjectionManifest | None:
        if not self.projection_path.is_file():
            return None
        try:
            with closing(_connect(self.projection_path)) as connection:
                rows = dict(connection.execute("SELECT key, value FROM projection_meta"))
        except sqlite3.DatabaseError:
            return None
        try:
            values = {key: json.loads(value) for key, value in rows.items()}
            return ProjectionManifest(
                projection_schema=str(values["projection_schema"]),
                built_from_ledger_head=str(values["built_from_ledger_head"]),
                last_sequence=int(values["last_sequence"]),
                built_at=str(values["built_at"]),
                builder_version=str(values["builder_version"]),
                logical_state_hash=str(values["logical_state_hash"]),
                memory_event_count=int(values["memory_event_count"]),
                claim_count=int(values["claim_count"]),
                evidence_count=int(values["evidence_count"]),
                decision_count=int(values["decision_count"]),
                conflict_count=int(values["conflict_count"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def freshness(self) -> ProjectionFreshness:
        verification = self.record_store.verify()
        ledger_head = verification.last_valid_hash or GENESIS_HASH
        if not verification.valid:
            return ProjectionFreshness(
                exists=self.projection_path.exists(),
                fresh=False,
                projection_head=None,
                ledger_head=ledger_head,
                reason="ledger_invalid",
            )
        manifest = self.read_manifest()
        if manifest is None:
            return ProjectionFreshness(
                exists=self.projection_path.exists(),
                fresh=False,
                projection_head=None,
                ledger_head=ledger_head,
                reason="projection_missing_or_invalid",
            )
        if manifest.projection_schema != PROJECTION_SCHEMA:
            return ProjectionFreshness(
                exists=True,
                fresh=False,
                projection_head=manifest.built_from_ledger_head,
                ledger_head=ledger_head,
                reason="projection_schema_mismatch",
            )
        if manifest.built_from_ledger_head != ledger_head:
            return ProjectionFreshness(
                exists=True,
                fresh=False,
                projection_head=manifest.built_from_ledger_head,
                ledger_head=ledger_head,
                reason="projection_stale",
            )
        return ProjectionFreshness(
            exists=True,
            fresh=True,
            projection_head=manifest.built_from_ledger_head,
            ledger_head=ledger_head,
            reason="ok",
        )
