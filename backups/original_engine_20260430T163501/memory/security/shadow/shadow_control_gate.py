from __future__ import annotations

import hashlib
import inspect
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from .shadow_audit import ShadowAudit
from .shadow_locking import ShadowLocking
from .shadow_tokens import ShadowTokenManager


@dataclass
class GateRequest:
    caller_id: str
    request_reason: str
    request_token: str


class ShadowControlGate:
    """Unified and audited entrypoint for high-risk shadow operations."""

    ALLOWED_CALLERS = {"memory_core", "repair_controller", "trusted_cli", "system_bootstrap"}

    def __init__(self, locking: ShadowLocking, tokens: ShadowTokenManager, audit: ShadowAudit) -> None:
        self.locking = locking
        self.tokens = tokens
        self.audit = audit
        self._integrity_fp = self._fingerprint()

    def _fingerprint(self) -> str:
        parts = []
        for name in ("_check", "execute"):
            fn = getattr(type(self), name, None)
            if fn is None:
                continue
            try:
                src = inspect.getsource(fn)
            except Exception:
                src = repr(fn)
            parts.append(f"{name}:{src}")
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()

    def _integrity_ok(self) -> bool:
        try:
            return self._integrity_fp == self._fingerprint()
        except Exception:
            return False

    def _check(self, req: GateRequest, permission: str) -> Optional[str]:
        if str(req.caller_id) not in self.ALLOWED_CALLERS:
            return "caller_not_allowed"
        if not self.tokens.validate_token(req.request_token, permission, req.caller_id):
            return "token_invalid_or_expired"
        return None

    def execute(
        self,
        *,
        op_name: str,
        permission: str,
        req: GateRequest,
        pre_state: str,
        callback: Callable[[str], Dict[str, Any]],
        cooldown_s: int = 0,
        ttl_s: int = 120,
        execute_timeout_s: float | None = None,
    ) -> Dict[str, Any]:
        op_id = f"op-{uuid.uuid4().hex[:10]}"
        if not self._integrity_ok():
            row = self.audit.append(
                {
                    "operation_id": op_id,
                    "request_type": op_name,
                    "caller_id": req.caller_id,
                    "request_reason": req.request_reason,
                    "pre_state": pre_state,
                    "post_state": pre_state,
                    "result": "rejected",
                    "error": "control_gate_integrity_mismatch",
                }
            )
            return {"status": "rejected", "reason": "control_gate_integrity_mismatch", "audit": row, "operation_id": op_id}
        err = self._check(req, permission)
        if err:
            row = self.audit.append(
                {
                    "operation_id": op_id,
                    "request_type": op_name,
                    "caller_id": req.caller_id,
                    "request_reason": req.request_reason,
                    "pre_state": pre_state,
                    "post_state": pre_state,
                    "result": "rejected",
                    "error": err,
                }
            )
            return {"status": "rejected", "reason": err, "audit": row, "operation_id": op_id}

        try:
            with self.locking.acquire(op_name, req.caller_id, ttl_s=ttl_s, cooldown_s=cooldown_s) as lease:
                started = time.monotonic()
                result = callback(lease.lease_id)
                elapsed = time.monotonic() - started
                timeout = float(execute_timeout_s if execute_timeout_s is not None else ttl_s)
                if elapsed > max(0.001, timeout):
                    self.audit.append(
                        {
                            "operation_id": op_id,
                            "request_type": op_name,
                            "caller_id": req.caller_id,
                            "request_reason": req.request_reason,
                            "pre_state": pre_state,
                            "post_state": pre_state,
                            "result": "rejected",
                            "error": "operation_timeout",
                            "elapsed_s": round(elapsed, 6),
                            "timeout_s": timeout,
                        }
                    )
                    return {
                        "status": "rejected",
                        "reason": "operation_timeout",
                        "operation_id": op_id,
                        "elapsed_s": round(elapsed, 6),
                        "timeout_s": timeout,
                    }
                post_state = str(result.get("post_state", pre_state))
                self.audit.append(
                    {
                        "operation_id": op_id,
                        "request_type": op_name,
                        "caller_id": req.caller_id,
                        "request_reason": req.request_reason,
                        "pre_state": pre_state,
                        "post_state": post_state,
                        "result": str(result.get("status", "success")),
                        "error": str(result.get("error", "")),
                        "elapsed_s": round(elapsed, 6),
                    }
                )
                return {"operation_id": op_id, **result}
        except Exception as exc:
            row = self.audit.append(
                {
                    "operation_id": op_id,
                    "request_type": op_name,
                    "caller_id": req.caller_id,
                    "request_reason": req.request_reason,
                    "pre_state": pre_state,
                    "post_state": pre_state,
                    "result": "rejected",
                    "error": str(exc),
                }
            )
            return {"status": "rejected", "reason": str(exc), "audit": row, "operation_id": op_id}
