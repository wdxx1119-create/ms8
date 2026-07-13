"""Immutable domain objects for the ledger-v1 memory model."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, cast

MEMORY_EVENT_KINDS = {
    "user_input",
    "conversation_turn",
    "document_fragment",
    "tool_output",
    "legacy_import",
    "system_observation",
}
CLAIM_KINDS = {"preference", "constraint", "decision", "fact", "task", "summary"}
CLAIM_STATUSES = {"proposed", "pending_review", "accepted", "verified", "disputed", "superseded", "revoked", "expired"}
EVIDENCE_RELATIONS = {"supports", "contradicts", "clarifies", "supersedes_basis"}
DECISION_ACTIONS = {
    "admit",
    "review_accept",
    "review_reject",
    "correct",
    "supersede",
    "revoke",
    "forget",
    "resolve_conflict",
    "expire",
}
ACTOR_KINDS = {"user", "reviewer", "mcp_client", "system", "migration"}
OBSERVED_PRECISIONS = {"exact", "legacy_inferred", "unknown"}
TRUST_CLASSES = {"user_explicit", "user_implicit", "untrusted_document", "tool_generated", "system_observed"}
VALID_TIME_BASES = {"user_explicit", "source_metadata", "inferred", "legacy_inferred", "unknown"}


def _require_text(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _parse_datetime(value: object, field_name: str) -> datetime:
    raw = _require_text(value, field_name)
    candidate = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _freeze_json(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("JSON values must not contain NaN or infinity")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("JSON object keys must be strings")
            frozen[key] = _freeze_json(item)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    raise TypeError(f"unsupported JSON value type: {type(value).__name__}")


def thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): thaw_json(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [thaw_json(v) for v in value]
    return value


def _freeze_mapping(value: object, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    return _freeze_json(value)


@dataclass(frozen=True, slots=True)
class Actor:
    kind: str
    id: str

    def __post_init__(self) -> None:
        kind = _require_text(self.kind, "actor.kind")
        if kind not in ACTOR_KINDS:
            raise ValueError(f"unsupported actor.kind: {kind}")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "id", _require_text(self.id, "actor.id"))

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "id": self.id}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Actor:
        return cls(kind=str(value.get("kind") or ""), id=str(value.get("id") or ""))


@dataclass(frozen=True, slots=True)
class ValidTime:
    start: str | None = None
    end: str | None = None
    basis: str = "unknown"

    def __post_init__(self) -> None:
        basis = _require_text(self.basis, "valid_time.basis")
        if basis not in VALID_TIME_BASES:
            raise ValueError(f"unsupported valid_time.basis: {basis}")
        start_dt = _parse_datetime(self.start, "valid_time.start") if self.start else None
        end_dt = _parse_datetime(self.end, "valid_time.end") if self.end else None
        if start_dt is not None and end_dt is not None and end_dt < start_dt:
            raise ValueError("valid_time.end must not precede valid_time.start")
        object.__setattr__(self, "basis", basis)

    def to_dict(self) -> dict[str, str | None]:
        return {"start": self.start, "end": self.end, "basis": self.basis}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> ValidTime:
        payload = value if isinstance(value, Mapping) else {}
        start = payload.get("start")
        end = payload.get("end")
        return cls(
            start=str(start) if start not in (None, "") else None,
            end=str(end) if end not in (None, "") else None,
            basis=str(payload.get("basis") or "unknown"),
        )


@dataclass(frozen=True, slots=True)
class MemoryEvent:
    event_id: str
    kind: str
    content: Mapping[str, Any]
    source: Mapping[str, Any]
    observed_at: str
    observed_at_precision: str = "exact"
    trust_class: str = "untrusted_document"

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_text(self.event_id, "event_id"))
        kind = _require_text(self.kind, "memory_event.kind")
        if kind not in MEMORY_EVENT_KINDS:
            raise ValueError(f"unsupported memory_event.kind: {kind}")
        object.__setattr__(self, "kind", kind)
        content = _freeze_mapping(self.content, "memory_event.content")
        source = _freeze_mapping(self.source, "memory_event.source")
        if not content:
            raise ValueError("memory_event.content must not be empty")
        if not source:
            raise ValueError("memory_event.source must not be empty")
        object.__setattr__(self, "content", content)
        object.__setattr__(self, "source", source)
        _parse_datetime(self.observed_at, "memory_event.observed_at")
        precision = _require_text(self.observed_at_precision, "observed_at_precision")
        if precision not in OBSERVED_PRECISIONS:
            raise ValueError(f"unsupported observed_at_precision: {precision}")
        object.__setattr__(self, "observed_at_precision", precision)
        trust_class = _require_text(self.trust_class, "trust_class")
        if trust_class not in TRUST_CLASSES:
            raise ValueError(f"unsupported trust_class: {trust_class}")
        object.__setattr__(self, "trust_class", trust_class)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "kind": self.kind,
            "content": thaw_json(self.content),
            "source": thaw_json(self.source),
            "observed_at": self.observed_at,
            "observed_at_precision": self.observed_at_precision,
            "trust_class": self.trust_class,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> MemoryEvent:
        raw_content = value.get("content")
        raw_source = value.get("source")
        content = cast(Mapping[str, Any], raw_content) if isinstance(raw_content, Mapping) else {}
        source = cast(Mapping[str, Any], raw_source) if isinstance(raw_source, Mapping) else {}
        return cls(
            event_id=str(value.get("event_id") or ""),
            kind=str(value.get("kind") or ""),
            content=content,
            source=source,
            observed_at=str(value.get("observed_at") or ""),
            observed_at_precision=str(value.get("observed_at_precision") or "exact"),
            trust_class=str(value.get("trust_class") or "untrusted_document"),
        )


@dataclass(frozen=True, slots=True)
class Claim:
    claim_id: str
    kind: str
    text: str
    subject: str
    predicate: str
    value: Any
    scope: str
    realm_id: str
    authority: str
    sensitivity: str
    confidence: float
    status: str
    valid_time: ValidTime
    created_from_event_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "claim_id", _require_text(self.claim_id, "claim_id"))
        kind = _require_text(self.kind, "claim.kind")
        if kind not in CLAIM_KINDS:
            raise ValueError(f"unsupported claim.kind: {kind}")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "text", _require_text(self.text, "claim.text"))
        object.__setattr__(self, "subject", _require_text(self.subject, "claim.subject"))
        object.__setattr__(self, "predicate", _require_text(self.predicate, "claim.predicate"))
        object.__setattr__(self, "value", _freeze_json(self.value))
        object.__setattr__(self, "scope", _require_text(self.scope, "claim.scope"))
        object.__setattr__(self, "realm_id", _require_text(self.realm_id, "claim.realm_id"))
        object.__setattr__(self, "authority", _require_text(self.authority, "claim.authority"))
        object.__setattr__(self, "sensitivity", _require_text(self.sensitivity, "claim.sensitivity"))
        try:
            confidence = float(self.confidence)
        except (TypeError, ValueError) as exc:
            raise ValueError("claim.confidence must be numeric") from exc
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise ValueError("claim.confidence must be between 0 and 1")
        object.__setattr__(self, "confidence", confidence)
        status = _require_text(self.status, "claim.status")
        if status not in CLAIM_STATUSES:
            raise ValueError(f"unsupported claim.status: {status}")
        object.__setattr__(self, "status", status)
        if not isinstance(self.valid_time, ValidTime):
            raise TypeError("claim.valid_time must be ValidTime")
        object.__setattr__(
            self,
            "created_from_event_id",
            _require_text(self.created_from_event_id, "claim.created_from_event_id"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "kind": self.kind,
            "text": self.text,
            "subject": self.subject,
            "predicate": self.predicate,
            "value": thaw_json(self.value),
            "scope": self.scope,
            "realm_id": self.realm_id,
            "authority": self.authority,
            "sensitivity": self.sensitivity,
            "confidence": self.confidence,
            "status": self.status,
            "valid_time": self.valid_time.to_dict(),
            "created_from_event_id": self.created_from_event_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Claim:
        return cls(
            claim_id=str(value.get("claim_id") or ""),
            kind=str(value.get("kind") or ""),
            text=str(value.get("text") or ""),
            subject=str(value.get("subject") or ""),
            predicate=str(value.get("predicate") or ""),
            value=value.get("value"),
            scope=str(value.get("scope") or ""),
            realm_id=str(value.get("realm_id") or ""),
            authority=str(value.get("authority") or ""),
            sensitivity=str(value.get("sensitivity") or ""),
            confidence=float(value.get("confidence", 0.0)),
            status=str(value.get("status") or ""),
            valid_time=ValidTime.from_dict(value.get("valid_time") if isinstance(value.get("valid_time"), Mapping) else None),
            created_from_event_id=str(value.get("created_from_event_id") or ""),
        )


@dataclass(frozen=True, slots=True)
class Evidence:
    evidence_id: str
    claim_id: str
    event_id: str
    relation: str
    fragment: Mapping[str, Any]
    quoted_text_hash: str
    weight: float = 1.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_id", _require_text(self.evidence_id, "evidence_id"))
        object.__setattr__(self, "claim_id", _require_text(self.claim_id, "evidence.claim_id"))
        object.__setattr__(self, "event_id", _require_text(self.event_id, "evidence.event_id"))
        relation = _require_text(self.relation, "evidence.relation")
        if relation not in EVIDENCE_RELATIONS:
            raise ValueError(f"unsupported evidence.relation: {relation}")
        object.__setattr__(self, "relation", relation)
        fragment = _freeze_mapping(self.fragment, "evidence.fragment")
        if not fragment:
            raise ValueError("evidence.fragment must not be empty")
        object.__setattr__(self, "fragment", fragment)
        object.__setattr__(self, "quoted_text_hash", _require_text(self.quoted_text_hash, "quoted_text_hash"))
        try:
            weight = float(self.weight)
        except (TypeError, ValueError) as exc:
            raise ValueError("evidence.weight must be numeric") from exc
        if not math.isfinite(weight) or weight < 0.0:
            raise ValueError("evidence.weight must be finite and non-negative")
        object.__setattr__(self, "weight", weight)

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "claim_id": self.claim_id,
            "event_id": self.event_id,
            "relation": self.relation,
            "fragment": thaw_json(self.fragment),
            "quoted_text_hash": self.quoted_text_hash,
            "weight": self.weight,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Evidence:
        raw_fragment = value.get("fragment")
        fragment = cast(Mapping[str, Any], raw_fragment) if isinstance(raw_fragment, Mapping) else {}
        return cls(
            evidence_id=str(value.get("evidence_id") or ""),
            claim_id=str(value.get("claim_id") or ""),
            event_id=str(value.get("event_id") or ""),
            relation=str(value.get("relation") or ""),
            fragment=fragment,
            quoted_text_hash=str(value.get("quoted_text_hash") or ""),
            weight=float(value.get("weight", 1.0)),
        )


@dataclass(frozen=True, slots=True)
class Decision:
    decision_id: str
    action: str
    target_claim_ids: tuple[str, ...] = field(default_factory=tuple)
    result_claim_id: str | None = None
    result_status: str | None = None
    policy: Mapping[str, Any] = field(default_factory=dict)
    actor: Actor = field(default_factory=lambda: Actor(kind="system", id="ms8"))
    reason: str = ""
    recorded_at: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision_id", _require_text(self.decision_id, "decision_id"))
        action = _require_text(self.action, "decision.action")
        if action not in DECISION_ACTIONS:
            raise ValueError(f"unsupported decision.action: {action}")
        object.__setattr__(self, "action", action)
        targets = tuple(_require_text(item, "decision.target_claim_ids[]") for item in self.target_claim_ids)
        if len(set(targets)) != len(targets):
            raise ValueError("decision.target_claim_ids must not contain duplicates")
        object.__setattr__(self, "target_claim_ids", targets)
        result_claim_id = str(self.result_claim_id or "").strip() or None
        object.__setattr__(self, "result_claim_id", result_claim_id)
        result_status = str(self.result_status or "").strip() or None
        if result_status is not None and result_status not in CLAIM_STATUSES:
            raise ValueError(f"unsupported decision.result_status: {result_status}")
        object.__setattr__(self, "result_status", result_status)
        if not targets and result_claim_id is None:
            raise ValueError("decision must target an existing claim or produce a result claim")
        object.__setattr__(self, "policy", _freeze_mapping(self.policy, "decision.policy"))
        if not isinstance(self.actor, Actor):
            raise TypeError("decision.actor must be Actor")
        object.__setattr__(self, "reason", _require_text(self.reason, "decision.reason"))
        _parse_datetime(self.recorded_at, "decision.recorded_at")

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "action": self.action,
            "target_claim_ids": list(self.target_claim_ids),
            "result_claim_id": self.result_claim_id,
            "result_status": self.result_status,
            "policy": thaw_json(self.policy),
            "actor": self.actor.to_dict(),
            "reason": self.reason,
            "recorded_at": self.recorded_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Decision:
        raw_targets = value.get("target_claim_ids")
        targets = tuple(str(item) for item in raw_targets) if isinstance(raw_targets, (list, tuple)) else ()
        raw_actor = value.get("actor")
        raw_policy = value.get("policy")
        actor_payload = cast(Mapping[str, Any], raw_actor) if isinstance(raw_actor, Mapping) else {}
        policy = cast(Mapping[str, Any], raw_policy) if isinstance(raw_policy, Mapping) else {}
        result_claim_id = value.get("result_claim_id")
        result_status = value.get("result_status")
        return cls(
            decision_id=str(value.get("decision_id") or ""),
            action=str(value.get("action") or ""),
            target_claim_ids=targets,
            result_claim_id=str(result_claim_id) if result_claim_id not in (None, "") else None,
            result_status=str(result_status) if result_status not in (None, "") else None,
            policy=policy,
            actor=Actor.from_dict(actor_payload),
            reason=str(value.get("reason") or ""),
            recorded_at=str(value.get("recorded_at") or ""),
        )
