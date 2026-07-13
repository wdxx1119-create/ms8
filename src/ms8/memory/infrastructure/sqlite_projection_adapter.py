"""Generic projection adapter for the SQLite read model."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from ..application.replay import ReplayState
from ..domain.ledger import canonical_json
from ..ports.projection import ProjectionBuildResult, ProjectionDescriptor, ProjectionFreshness
from .durable_io import replace_path
from .projection_io import sha256_bytes
from .sqlite_projection import (
    PROJECTION_SCHEMA,
    _build_manifest,
    _connect,
    _create_schema,
    _write_state,
)

SQLITE_PROJECTION_NAME = "sqlite"


def _json_int(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("boolean is not a valid integer metadata value")
    if isinstance(value, int | str):
        return int(value)
    raise ValueError("invalid integer metadata value")


def _logical_state_hash(connection: sqlite3.Connection, values: dict[str, object]) -> str:
    memory_event_ids = [row[0] for row in connection.execute("SELECT event_id FROM memory_events ORDER BY event_id")]
    claims = []
    for claim_id, current_status, decision_ids_json, payload_json in connection.execute(
        "SELECT claim_id, current_status, decision_ids_json, payload_json FROM claims ORDER BY claim_id"
    ):
        claims.append(
            {
                "claim_id": claim_id,
                "current_status": current_status,
                "decision_ids": json.loads(decision_ids_json),
                "claim": json.loads(payload_json),
            }
        )
    evidence_ids = [row[0] for row in connection.execute("SELECT evidence_id FROM evidence ORDER BY evidence_id")]
    decision_ids = [row[0] for row in connection.execute("SELECT decision_id FROM decisions ORDER BY decision_id")]
    conflicts = [
        json.loads(row[0])
        for row in connection.execute("SELECT payload_json FROM conflicts ORDER BY conflict_id")
    ]
    payload = {
        "ledger_head": str(values["built_from_ledger_head"]),
        "last_sequence": _json_int(values["last_sequence"]),
        "memory_event_ids": memory_event_ids,
        "claims": claims,
        "evidence_ids": evidence_ids,
        "decision_ids": decision_ids,
        "conflicts": conflicts,
    }
    return sha256_bytes(canonical_json(payload).encode("utf-8"))


class SQLiteProjectionAdapter:
    """Build the SQLite read model from a shared verified replay state."""

    def __init__(self, artifact_path: Path):
        self.artifact_path = Path(artifact_path)

    @property
    def name(self) -> str:
        return SQLITE_PROJECTION_NAME

    def rebuild_from_state(self, source: ReplayState) -> ProjectionBuildResult:
        manifest = _build_manifest(source)
        self.artifact_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.artifact_path.with_name(f".{self.artifact_path.name}.{uuid4().hex}.tmp")
        replaced = self.artifact_path.exists()
        try:
            connection = _connect(temporary)
            try:
                with connection:
                    _create_schema(connection)
                    _write_state(connection, source, manifest)
                connection.execute("PRAGMA wal_checkpoint(FULL)")
            finally:
                connection.close()
            with temporary.open("r+b") as handle:
                os.fsync(handle.fileno())
            replace_path(temporary, self.artifact_path)
        finally:
            temporary.unlink(missing_ok=True)
        descriptor = ProjectionDescriptor(
            name=self.name,
            schema=PROJECTION_SCHEMA,
            artifact_path=self.artifact_path,
            built_from_ledger_head=manifest.built_from_ledger_head,
            last_sequence=manifest.last_sequence,
            logical_state_hash=manifest.logical_state_hash,
            builder_version=manifest.builder_version,
            artifact_hash=sha256_bytes(self.artifact_path.read_bytes()),
        )
        return ProjectionBuildResult(descriptor=descriptor, replaced_existing=replaced)

    def read_descriptor(self) -> ProjectionDescriptor | None:
        if not self.artifact_path.is_file():
            return None
        try:
            with closing(_connect(self.artifact_path)) as connection:
                rows = dict(connection.execute("SELECT key, value FROM projection_meta"))
                values: dict[str, object] = {key: json.loads(value) for key, value in rows.items()}
                computed_hash = _logical_state_hash(connection, values)
            stored_hash = str(values["logical_state_hash"])
            if computed_hash != stored_hash:
                return None
            return ProjectionDescriptor(
                name=self.name,
                schema=str(values["projection_schema"]),
                artifact_path=self.artifact_path,
                built_from_ledger_head=str(values["built_from_ledger_head"]),
                last_sequence=_json_int(values["last_sequence"]),
                logical_state_hash=stored_hash,
                builder_version=str(values["builder_version"]),
                artifact_hash=sha256_bytes(self.artifact_path.read_bytes()),
            )
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError, sqlite3.DatabaseError):
            return None

    def freshness(self, ledger_head: str) -> ProjectionFreshness:
        descriptor = self.read_descriptor()
        if descriptor is None:
            return ProjectionFreshness(
                name=self.name,
                exists=self.artifact_path.exists(),
                fresh=False,
                projection_head=None,
                ledger_head=ledger_head,
                reason="projection_missing_or_invalid",
            )
        if descriptor.schema != PROJECTION_SCHEMA:
            return ProjectionFreshness(
                name=self.name,
                exists=True,
                fresh=False,
                projection_head=descriptor.built_from_ledger_head,
                ledger_head=ledger_head,
                reason="projection_schema_mismatch",
                logical_state_hash=descriptor.logical_state_hash,
            )
        if descriptor.built_from_ledger_head != ledger_head:
            return ProjectionFreshness(
                name=self.name,
                exists=True,
                fresh=False,
                projection_head=descriptor.built_from_ledger_head,
                ledger_head=ledger_head,
                reason="projection_stale",
                logical_state_hash=descriptor.logical_state_hash,
            )
        return ProjectionFreshness(
            name=self.name,
            exists=True,
            fresh=True,
            projection_head=descriptor.built_from_ledger_head,
            ledger_head=ledger_head,
            reason="ok",
            logical_state_hash=descriptor.logical_state_hash,
        )


__all__ = ["SQLITE_PROJECTION_NAME", "SQLiteProjectionAdapter"]
