"""PolicyEngine-backed authorization contract for automated lifecycle mutations."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

from ..domain.ledger import canonical_json
from ..domain.models import Actor, _parse_datetime

LIFECYCLE_POLICY_GRANT_SCHEMA = "ms8.lifecycle-policy-grant.v1"
_AUTOMATED_ACTOR_KINDS = frozenset({"system", "mcp_client"})


class LifecycleAuthorizationError(ValueError):
    """Raised when an automated lifecycle mutation lacks valid policy authorization."""


@dataclass(frozen=True, slots=True)
class LifecyclePolicyGrant:
    schema: str
    grant_id: str
    policy_engine: str
    policy_version: str
    actor_kind: str
    actor_id: str
    allowed_actions: tuple[str, ...]
    target_claim_ids: tuple[str, ...]
    issued_at: str
    expires_at: str
    nonce: str
    decision_hash: str

    @classmethod
    def create(
        cls,
        *,
        grant_id: str,
        policy_engine: str,
        policy_version: str,
        actor: Actor,
        allowed_actions: Sequence[str],
        target_claim_ids: Sequence[str],
        issued_at: str,
        expires_at: str,
        nonce: str,
    ) -> LifecyclePolicyGrant:
        normalized_actions = tuple(
            dict.fromkeys(str(item).strip() for item in allowed_actions if str(item).strip())
        )
        normalized_claims = tuple(
            dict.fromkeys(str(item).strip() for item in target_claim_ids if str(item).strip())
        )
        payload = {
            "schema": LIFECYCLE_POLICY_GRANT_SCHEMA,
            "grant_id": str(grant_id).strip(),
            "policy_engine": str(policy_engine).strip(),
            "policy_version": str(policy_version).strip(),
            "actor_kind": actor.kind,
            "actor_id": actor.id,
            "allowed_actions": list(normalized_actions),
            "target_claim_ids": list(normalized_claims),
            "issued_at": issued_at,
            "expires_at": expires_at,
            "nonce": str(nonce).strip(),
        }
        decision_hash = "sha256:" + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
        return cls(
            schema=LIFECYCLE_POLICY_GRANT_SCHEMA,
            grant_id=str(payload["grant_id"]),
            policy_engine=str(payload["policy_engine"]),
            policy_version=str(payload["policy_version"]),
            actor_kind=actor.kind,
            actor_id=actor.id,
            allowed_actions=normalized_actions,
            target_claim_ids=normalized_claims,
            issued_at=issued_at,
            expires_at=expires_at,
            nonce=str(payload["nonce"]),
            decision_hash=decision_hash,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> LifecyclePolicyGrant:
        raw_actions = value.get("allowed_actions")
        raw_claims = value.get("target_claim_ids")
        actions = (
            tuple(str(item) for item in raw_actions)
            if isinstance(raw_actions, (list, tuple))
            else ()
        )
        claims = (
            tuple(str(item) for item in raw_claims)
            if isinstance(raw_claims, (list, tuple))
            else ()
        )
        return cls(
            schema=str(value.get("schema") or ""),
            grant_id=str(value.get("grant_id") or ""),
            policy_engine=str(value.get("policy_engine") or ""),
            policy_version=str(value.get("policy_version") or ""),
            actor_kind=str(value.get("actor_kind") or ""),
            actor_id=str(value.get("actor_id") or ""),
            allowed_actions=actions,
            target_claim_ids=claims,
            issued_at=str(value.get("issued_at") or ""),
            expires_at=str(value.get("expires_at") or ""),
            nonce=str(value.get("nonce") or ""),
            decision_hash=str(value.get("decision_hash") or ""),
        )

    def payload_without_hash(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "grant_id": self.grant_id,
            "policy_engine": self.policy_engine,
            "policy_version": self.policy_version,
            "actor_kind": self.actor_kind,
            "actor_id": self.actor_id,
            "allowed_actions": list(self.allowed_actions),
            "target_claim_ids": list(self.target_claim_ids),
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "nonce": self.nonce,
        }

    def to_dict(self) -> dict[str, object]:
        return {**self.payload_without_hash(), "decision_hash": self.decision_hash}

    def verify_integrity(self) -> bool:
        expected = "sha256:" + hashlib.sha256(
            canonical_json(self.payload_without_hash()).encode("utf-8")
        ).hexdigest()
        return self.schema == LIFECYCLE_POLICY_GRANT_SCHEMA and self.decision_hash == expected


@runtime_checkable
class LifecyclePolicyVerifier(Protocol):
    def __call__(self, grant: LifecyclePolicyGrant) -> bool:
        """Return true only when the configured PolicyEngine recognizes the grant."""


LifecyclePolicyVerifierFn = Callable[[LifecyclePolicyGrant], bool]


def _utc(value: str, field_name: str) -> datetime:
    return _parse_datetime(value, field_name).astimezone(timezone.utc)


def require_lifecycle_authorization(
    *,
    action: str,
    target_claim_ids: Sequence[str],
    actor: Actor,
    recorded_at: str,
    grant: LifecyclePolicyGrant | None,
    verifier: LifecyclePolicyVerifierFn | None,
) -> LifecyclePolicyGrant | None:
    """Require a verified PolicyEngine grant for automated actors; users remain explicit."""

    if actor.kind not in _AUTOMATED_ACTOR_KINDS:
        return grant
    if verifier is None:
        raise LifecycleAuthorizationError(
            "automated lifecycle mutation requires a configured PolicyEngine verifier"
        )
    if grant is None:
        raise LifecycleAuthorizationError(
            "automated lifecycle mutation requires a PolicyEngine grant"
        )
    if not grant.verify_integrity():
        raise LifecycleAuthorizationError("PolicyEngine grant integrity verification failed")
    if (grant.actor_kind, grant.actor_id) != (actor.kind, actor.id):
        raise LifecycleAuthorizationError("PolicyEngine grant actor does not match mutation actor")
    if action not in grant.allowed_actions:
        raise LifecycleAuthorizationError("PolicyEngine grant does not authorize this action")
    required_claims = {str(item).strip() for item in target_claim_ids if str(item).strip()}
    if not required_claims.issubset(set(grant.target_claim_ids)):
        raise LifecycleAuthorizationError("PolicyEngine grant does not cover all target claims")
    instant = _utc(recorded_at, "recorded_at")
    if instant < _utc(grant.issued_at, "grant.issued_at"):
        raise LifecycleAuthorizationError("PolicyEngine grant is not active yet")
    if instant > _utc(grant.expires_at, "grant.expires_at"):
        raise LifecycleAuthorizationError("PolicyEngine grant has expired")
    if not verifier(grant):
        raise LifecycleAuthorizationError("PolicyEngine rejected the lifecycle grant")
    return grant


__all__ = [
    "LIFECYCLE_POLICY_GRANT_SCHEMA",
    "LifecycleAuthorizationError",
    "LifecyclePolicyGrant",
    "LifecyclePolicyVerifier",
    "LifecyclePolicyVerifierFn",
    "require_lifecycle_authorization",
]
