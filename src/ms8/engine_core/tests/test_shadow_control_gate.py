from __future__ import annotations

import tempfile
import time
from pathlib import Path

from ms8.engine_core.security.shadow import get_shadow_system
from ms8.engine_core.security.shadow.shadow_control_gate import GateRequest


def _cfg(base: Path) -> dict:
    return {
        "memory_dir": base,
        "settings": {
            "memory": {
                "security": {
                    "shadow": {
                        "enabled": True,
                        "shadow_dir": str(base / "security" / "shadow_data"),
                        "payload_threshold_chars": 16,
                        "checkpoint_interval": 3,
                        "snapshot_interval": 3,
                        "snapshot_keep": 2,
                    }
                }
            }
        },
    }


def test_recover_target_whitelist_rejects_unknown() -> None:
    base = Path(tempfile.mkdtemp(prefix="shadow_gate_"))
    shadow = get_shadow_system(_cfg(base))
    out = shadow.recover_from_events(target="unknown_target")
    assert out["status"] == "rejected"


def test_concurrent_high_risk_operation_rejected_by_lock() -> None:
    base = Path(tempfile.mkdtemp(prefix="shadow_gate_lock_"))
    shadow = get_shadow_system(_cfg(base))
    shadow.trigger_seal("unit")
    shadow.spool_write("A", "test")
    shadow.bind_recovery_target("main_memory", lambda _t, _s, _m: None)

    with shadow.locking.acquire("manual_hold", "test", ttl_s=30, cooldown_s=0):
        out = shadow.replay_spool(target="main_memory")
        assert out["status"] == "rejected"
        assert "operation_locked" in str(out.get("reason", ""))


def test_backup_sync_rejects_unknown_caller() -> None:
    base = Path(tempfile.mkdtemp(prefix="shadow_gate_backup_sync_"))
    shadow = get_shadow_system(_cfg(base))
    out = shadow.sync_verified_backup(caller_id="unknown_actor", request_token="")
    assert out["status"] == "rejected"


def test_issued_token_allows_then_revoke_blocks_operation() -> None:
    base = Path(tempfile.mkdtemp(prefix="shadow_gate_token_revoke_"))
    shadow = get_shadow_system(_cfg(base))
    token = shadow.issue_capability_token(
        "trusted_cli",
        permissions=["seal:trigger"],
        ttl_seconds=300,
    )
    ok_out = shadow.trigger_seal(
        "token_issue_ok",
        caller_id="trusted_cli",
        request_token=token,
    )
    assert bool(ok_out.get("sealed", False)) is True
    assert bool(ok_out.get("operation_id", ""))
    shadow.revoke_capability_token(token)
    bad_out = shadow.trigger_seal(
        "token_revoked_should_block",
        caller_id="trusted_cli",
        request_token=token,
    )
    assert str(bad_out.get("status")) == "rejected"


def test_clear_seal_requires_reason_and_session_match() -> None:
    base = Path(tempfile.mkdtemp(prefix="shadow_gate_unseal_guard_"))
    shadow = get_shadow_system(_cfg(base))
    seal_out = shadow.trigger_seal("unit-guard")
    assert bool(seal_out.get("sealed", False)) is True
    bad = shadow.clear_seal(
        reason="manual",
        caller_id="memory_core",
        expected_seal_reason="wrong",
        expected_seal_session_id="wrong",
    )
    assert str(bad.get("status")) == "rejected"
    st = shadow.status().get("manifest", {})
    good = shadow.clear_seal(
        reason="manual",
        caller_id="memory_core",
        expected_seal_reason=str(st.get("reason", "")),
        expected_seal_session_id=str(st.get("seal_session_id", "")),
    )
    assert str(good.get("mode", "")) == "active"


def test_gate_rejects_timeout_overrun() -> None:
    base = Path(tempfile.mkdtemp(prefix="shadow_gate_timeout_"))
    shadow = get_shadow_system(_cfg(base))
    token = shadow.issue_capability_token(
        "trusted_cli",
        permissions=["seal:trigger"],
        ttl_seconds=300,
    )

    def _slow(_lease_id: str):
        time.sleep(0.02)
        return {"status": "success", "post_state": "active"}

    out = shadow.gate.execute(
        op_name="timeout_probe",
        permission="seal:trigger",
        req=GateRequest(caller_id="trusted_cli", request_reason="unit_timeout", request_token=token),
        pre_state="active",
        callback=_slow,
        cooldown_s=0,
        ttl_s=60,
        execute_timeout_s=0.001,
    )
    assert str(out.get("status")) == "rejected"
    assert str(out.get("reason")) == "operation_timeout"
