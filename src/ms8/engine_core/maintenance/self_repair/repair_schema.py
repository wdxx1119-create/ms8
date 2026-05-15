from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RepairPolicy:
    check_id: str
    action: str
    domain: str
    risk: str  # R1 | R2 | R3
    depends_on: list[str] = field(default_factory=list)
    target: str = ""


@dataclass
class RepairPlanItem:
    operation_id: str
    check_id: str
    action: str
    domain: str
    risk: str
    reason: str
    target: str
    depends_on: list[str] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    action_guide: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "check_id": self.check_id,
            "action": self.action,
            "domain": self.domain,
            "risk": self.risk,
            "reason": self.reason,
            "target": self.target,
            "depends_on": list(self.depends_on),
            "params": dict(self.params),
            "action_guide": self.action_guide,
        }


@dataclass
class RepairExecutionRow:
    operation_id: str
    check_id: str
    action: str
    domain: str
    risk: str
    mode: str
    result: str
    verify_status: str
    error: str = ""
    rolled_back: bool = False
    duration_ms: int = 0
    action_fingerprint: str = ""
    idempotency_key: str = ""
    policy_version: str = "v1"
    timestamp: str = field(default_factory=utc_now_iso)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "check_id": self.check_id,
            "action": self.action,
            "domain": self.domain,
            "risk": self.risk,
            "mode": self.mode,
            "result": self.result,
            "verify_status": self.verify_status,
            "error": self.error,
            "rolled_back": bool(self.rolled_back),
            "duration_ms": int(self.duration_ms),
            "action_fingerprint": self.action_fingerprint,
            "idempotency_key": self.idempotency_key,
            "policy_version": self.policy_version,
            "timestamp": self.timestamp,
            "details": dict(self.details),
        }
