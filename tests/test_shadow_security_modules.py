from __future__ import annotations

import time
from contextlib import contextmanager
from pathlib import Path

from ms8.engine_core.security.shadow import shadow_audit as sa
from ms8.engine_core.security.shadow.shadow_capacity_guard import ShadowCapacityGuard
from ms8.engine_core.security.shadow.shadow_control_gate import GateRequest, ShadowControlGate
from ms8.engine_core.security.shadow.shadow_tokens import ShadowTokenManager


def test_shadow_token_manager_issue_validate_revoke_and_expire() -> None:
    tm = ShadowTokenManager()
    token = tm.issue_token("memory_core", ["seal", "recover"], ttl_seconds=60)
    assert tm.validate_token(token, "seal", "memory_core") is True
    assert tm.validate_token(token, "missing", "memory_core") is False
    assert tm.validate_token(token, "seal", "other") is False

    # expire path
    tm._registry[token]["exp"] = int(time.time()) - 1
    assert tm.validate_token(token, "seal", "memory_core") is False

    # revoke path
    token2 = tm.issue_token("memory_core", ["seal"], ttl_seconds=60)
    tm.revoke_token(token2)
    assert tm.validate_token(token2, "seal", "memory_core") is False


def test_shadow_capacity_guard_usage_and_stages(tmp_path: Path) -> None:
    payloads = tmp_path / "payloads"
    payloads.mkdir(parents=True, exist_ok=True)
    (tmp_path / "a.bin").write_bytes(b"x" * 8)
    (payloads / "b.bin").write_bytes(b"y" * 6)
    guard = ShadowCapacityGuard(
        tmp_path,
        shadow_max_mb=0.00001,
        payload_max_mb=0.00001,
        enter_pct=0.95,
        alert_pct=0.85,
        warn_pct=0.70,
    )
    usage = guard.usage()
    assert usage["shadow_total"] >= 14
    assert usage["payload_total"] >= 6

    # default with tiny files can still be ok (min cap is 1MB)
    ev = guard.evaluate()
    assert ev["stage"] in {"ok", "warning", "alert", "critical"}
    assert "limits" in ev

    # force each stage by monkeypatching usage
    guard.usage = lambda: {"shadow_total": int(guard.shadow_max_bytes * 0.75), "payload_total": 0}  # type: ignore[assignment]
    assert guard.evaluate()["stage"] == "warning"
    guard.usage = lambda: {"shadow_total": int(guard.shadow_max_bytes * 0.90), "payload_total": 0}  # type: ignore[assignment]
    assert guard.evaluate()["stage"] == "alert"
    guard.usage = lambda: {"shadow_total": int(guard.shadow_max_bytes * 0.99), "payload_total": 0}  # type: ignore[assignment]
    assert guard.evaluate()["stage"] == "critical"


def test_shadow_audit_append_and_immutable_calls(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(sa, "set_mutable", lambda p, enabled=True: calls.append(("mutable", str(p))))
    monkeypatch.setattr(sa, "set_immutable", lambda p, enabled=False: calls.append(("immutable", str(p))))
    audit = sa.ShadowAudit(tmp_path, immutable_enabled=True)
    row = audit.append({"op": "seal"})
    assert row["op"] == "seal"
    assert "ts" in row
    text = (tmp_path / "ops_audit.jsonl").read_text(encoding="utf-8")
    assert '"op": "seal"' in text
    assert any(c[0] == "mutable" for c in calls)
    assert any(c[0] == "immutable" for c in calls)


class _AuditStub:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def append(self, row: dict) -> dict:
        self.rows.append(dict(row))
        return row


class _Lease:
    lease_id = "lease-1"


class _LockingStub:
    def __init__(self, *, raise_err: Exception | None = None) -> None:
        self.raise_err = raise_err

    @contextmanager
    def acquire(self, _name: str, _caller: str, ttl_s: int = 0, cooldown_s: int = 0):
        if self.raise_err is not None:
            raise self.raise_err
        _ = ttl_s, cooldown_s
        yield _Lease()


def test_shadow_control_gate_reject_paths_and_success(monkeypatch) -> None:
    tokens = ShadowTokenManager()
    audit = _AuditStub()
    lock = _LockingStub()
    gate = ShadowControlGate(lock, tokens, audit)

    # integrity mismatch
    monkeypatch.setattr(gate, "_integrity_ok", lambda: False)
    req = GateRequest(caller_id="memory_core", request_reason="x", request_token="t")
    out_int = gate.execute(
        op_name="seal",
        permission="seal",
        req=req,
        pre_state="open",
        callback=lambda _lease: {"status": "success", "post_state": "sealed"},
    )
    assert out_int["status"] == "rejected"
    assert out_int["reason"] == "control_gate_integrity_mismatch"

    # caller not allowed
    monkeypatch.setattr(gate, "_integrity_ok", lambda: True)
    req_bad = GateRequest(caller_id="evil", request_reason="x", request_token="t")
    out_caller = gate.execute(
        op_name="seal",
        permission="seal",
        req=req_bad,
        pre_state="open",
        callback=lambda _lease: {"status": "success", "post_state": "sealed"},
    )
    assert out_caller["status"] == "rejected"
    assert out_caller["reason"] == "caller_not_allowed"

    # token invalid
    req_invalid = GateRequest(caller_id="memory_core", request_reason="x", request_token="bad")
    out_tok = gate.execute(
        op_name="seal",
        permission="seal",
        req=req_invalid,
        pre_state="open",
        callback=lambda _lease: {"status": "success", "post_state": "sealed"},
    )
    assert out_tok["status"] == "rejected"
    assert out_tok["reason"] == "token_invalid_or_expired"

    # success path
    tok = tokens.issue_token("memory_core", ["seal"], ttl_seconds=120)
    req_ok = GateRequest(caller_id="memory_core", request_reason="x", request_token=tok)
    out_ok = gate.execute(
        op_name="seal",
        permission="seal",
        req=req_ok,
        pre_state="open",
        callback=lambda _lease: {"status": "success", "post_state": "sealed"},
    )
    assert out_ok["status"] == "success"
    assert out_ok["post_state"] == "sealed"


def test_shadow_control_gate_timeout_and_lock_error() -> None:
    tokens = ShadowTokenManager()
    audit = _AuditStub()
    tok = tokens.issue_token("memory_core", ["seal"], ttl_seconds=120)
    req = GateRequest(caller_id="memory_core", request_reason="x", request_token=tok)

    # timeout path
    gate_timeout = ShadowControlGate(_LockingStub(), tokens, audit)
    out_timeout = gate_timeout.execute(
        op_name="seal",
        permission="seal",
        req=req,
        pre_state="open",
        execute_timeout_s=0.01,
        callback=lambda _lease: (time.sleep(0.02) or {"status": "success", "post_state": "sealed"}),
    )
    assert out_timeout["status"] == "rejected"
    assert out_timeout["reason"] == "operation_timeout"

    # locking exception path
    gate_err = ShadowControlGate(_LockingStub(raise_err=RuntimeError("lock busy")), tokens, audit)
    out_err = gate_err.execute(
        op_name="seal",
        permission="seal",
        req=req,
        pre_state="open",
        callback=lambda _lease: {"status": "success", "post_state": "sealed"},
    )
    assert out_err["status"] == "rejected"
    assert "lock busy" in out_err["reason"]
