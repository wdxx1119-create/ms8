"""Deterministic transaction envelope for the authoritative memory ledger."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, cast
from uuid import uuid4

from .models import Actor, _freeze_mapping, _parse_datetime, _require_text, thaw_json

LEDGER_SCHEMA = "ms8.ledger.v1"
GENESIS_HASH = "sha256:" + ("0" * 64)
_HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_EVENT_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")


def canonical_json(value: Any) -> str:
    """Serialize a JSON value deterministically for hashing and JSONL storage."""

    return json.dumps(
        thaw_json(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _hash_payload(payload: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _validate_hash(value: object, field_name: str) -> str:
    raw = _require_text(value, field_name)
    if _HASH_PATTERN.fullmatch(raw) is None:
        raise ValueError(f"{field_name} must use sha256:<64 lowercase hex>")
    return raw


@dataclass(frozen=True, slots=True)
class LedgerEvent:
    """One immutable event inside an atomic ledger transaction."""

    type: str
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        event_type = _require_text(self.type, "ledger_event.type")
        if _EVENT_TYPE_PATTERN.fullmatch(event_type) is None:
            raise ValueError("ledger_event.type must be a namespaced lowercase identifier")
        object.__setattr__(self, "type", event_type)
        payload = _freeze_mapping(self.payload, "ledger_event.payload")
        if not payload:
            raise ValueError("ledger_event.payload must not be empty")
        object.__setattr__(self, "payload", payload)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "payload": thaw_json(self.payload)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> LedgerEvent:
        raw_payload = value.get("payload")
        payload = cast(Mapping[str, Any], raw_payload) if isinstance(raw_payload, Mapping) else {}
        return cls(type=str(value.get("type") or ""), payload=payload)


@dataclass(frozen=True, slots=True)
class TransactionVerification:
    valid: bool
    reason_codes: tuple[str, ...]
    transaction_id: str
    sequence: int
    hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "reason_codes": list(self.reason_codes),
            "transaction_id": self.transaction_id,
            "sequence": self.sequence,
            "hash": self.hash,
        }


@dataclass(frozen=True, slots=True)
class LedgerTransaction:
    """One complete, hash-chained, atomic line in ``events.jsonl``."""

    schema: str
    transaction_id: str
    sequence: int
    recorded_at: str
    actor: Actor
    prev_hash: str
    events: tuple[LedgerEvent, ...]
    hash: str

    def __post_init__(self) -> None:
        if self.schema != LEDGER_SCHEMA:
            raise ValueError(f"unsupported ledger schema: {self.schema}")
        object.__setattr__(self, "transaction_id", _require_text(self.transaction_id, "transaction_id"))
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 1:
            raise ValueError("sequence must be a positive integer")
        _parse_datetime(self.recorded_at, "recorded_at")
        if not isinstance(self.actor, Actor):
            raise TypeError("actor must be Actor")
        object.__setattr__(self, "prev_hash", _validate_hash(self.prev_hash, "prev_hash"))
        events = tuple(self.events)
        if not events:
            raise ValueError("events must contain at least one ledger event")
        if not all(isinstance(event, LedgerEvent) for event in events):
            raise TypeError("events must contain only LedgerEvent objects")
        object.__setattr__(self, "events", events)
        object.__setattr__(self, "hash", _validate_hash(self.hash, "hash"))

    @staticmethod
    def _unsigned_payload(
        *,
        schema: str,
        transaction_id: str,
        sequence: int,
        recorded_at: str,
        actor: Actor,
        prev_hash: str,
        events: Sequence[LedgerEvent],
    ) -> dict[str, Any]:
        return {
            "schema": schema,
            "transaction_id": transaction_id,
            "sequence": sequence,
            "recorded_at": recorded_at,
            "actor": actor.to_dict(),
            "prev_hash": prev_hash,
            "events": [event.to_dict() for event in events],
        }

    @classmethod
    def create(
        cls,
        *,
        sequence: int,
        actor: Actor,
        events: Sequence[LedgerEvent],
        prev_hash: str = GENESIS_HASH,
        transaction_id: str | None = None,
        recorded_at: str | None = None,
    ) -> LedgerTransaction:
        txn_id = transaction_id or f"txn_{uuid4().hex}"
        timestamp = recorded_at or datetime.now(timezone.utc).isoformat()
        event_tuple = tuple(events)
        payload = cls._unsigned_payload(
            schema=LEDGER_SCHEMA,
            transaction_id=txn_id,
            sequence=sequence,
            recorded_at=timestamp,
            actor=actor,
            prev_hash=prev_hash,
            events=event_tuple,
        )
        return cls(
            schema=LEDGER_SCHEMA,
            transaction_id=txn_id,
            sequence=sequence,
            recorded_at=timestamp,
            actor=actor,
            prev_hash=prev_hash,
            events=event_tuple,
            hash=_hash_payload(payload),
        )

    def unsigned_dict(self) -> dict[str, Any]:
        return self._unsigned_payload(
            schema=self.schema,
            transaction_id=self.transaction_id,
            sequence=self.sequence,
            recorded_at=self.recorded_at,
            actor=self.actor,
            prev_hash=self.prev_hash,
            events=self.events,
        )

    def calculate_hash(self) -> str:
        return _hash_payload(self.unsigned_dict())

    def to_dict(self) -> dict[str, Any]:
        return {**self.unsigned_dict(), "hash": self.hash}

    def to_json_line(self) -> str:
        return canonical_json(self.to_dict())

    def verify(
        self,
        *,
        expected_prev_hash: str | None = None,
        expected_sequence: int | None = None,
    ) -> TransactionVerification:
        reasons: list[str] = []
        if self.calculate_hash() != self.hash:
            reasons.append("transaction_hash_mismatch")
        if expected_prev_hash is not None and self.prev_hash != expected_prev_hash:
            reasons.append("previous_hash_mismatch")
        if expected_sequence is not None and self.sequence != expected_sequence:
            reasons.append("sequence_mismatch")
        return TransactionVerification(
            valid=not reasons,
            reason_codes=tuple(reasons),
            transaction_id=self.transaction_id,
            sequence=self.sequence,
            hash=self.hash,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], *, verify_hash: bool = True) -> LedgerTransaction:
        raw_actor = value.get("actor")
        actor_payload = cast(Mapping[str, Any], raw_actor) if isinstance(raw_actor, Mapping) else {}
        raw_events = value.get("events")
        if not isinstance(raw_events, list):
            raise TypeError("events must be an array")
        events: list[LedgerEvent] = []
        for item in raw_events:
            if not isinstance(item, Mapping):
                raise TypeError("events must contain objects")
            events.append(LedgerEvent.from_dict(item))
        transaction = cls(
            schema=str(value.get("schema") or ""),
            transaction_id=str(value.get("transaction_id") or ""),
            sequence=cast(int, value.get("sequence")),
            recorded_at=str(value.get("recorded_at") or ""),
            actor=Actor.from_dict(actor_payload),
            prev_hash=str(value.get("prev_hash") or ""),
            events=tuple(events),
            hash=str(value.get("hash") or ""),
        )
        if verify_hash:
            result = transaction.verify()
            if not result.valid:
                raise ValueError(",".join(result.reason_codes))
        return transaction

    @classmethod
    def from_json_line(cls, line: str, *, verify_hash: bool = True) -> LedgerTransaction:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError("invalid ledger JSON") from exc
        if not isinstance(payload, Mapping):
            raise TypeError("ledger transaction must be a JSON object")
        return cls.from_dict(payload, verify_hash=verify_hash)
