from __future__ import annotations

from pathlib import Path

from ms8.engine_core.security.shadow.shadow_guard import ShadowSystem


def _config(tmp_path: Path):
    return {
        "memory_dir": tmp_path / "memory",
        "workspace_dir": tmp_path / "ws",
        "settings": {
            "memory": {
                "security": {
                    "shadow": {
                        "enabled": True,
                        "shadow_dir": str(tmp_path / "shadow"),
                        "backup_dir": str(tmp_path / "shadow_backup"),
                        "immutable_enabled": False,
                        "stack_guard_enabled": False,
                        "auto_self_heal_on_startup": False,
                    }
                }
            }
        },
    }


def _direct_gate(system: ShadowSystem, monkeypatch) -> None:
    def _exec(**kwargs):
        cb = kwargs["callback"]
        result = cb("lease-1")
        return {"status": "success", "operation_id": "op-1", "result": result}

    monkeypatch.setattr(system.gate, "execute", _exec)


def test_clear_seal_identity_paths(tmp_path: Path, monkeypatch) -> None:
    cfg = _config(tmp_path)
    cfg["memory_dir"].mkdir(parents=True, exist_ok=True)
    cfg["workspace_dir"].mkdir(parents=True, exist_ok=True)
    system = ShadowSystem(cfg)
    monkeypatch.setattr(system, "_stack_guard_ok", lambda: True)

    # identity required
    monkeypatch.setattr(system._seal, "status", lambda: {"reason": "r1", "seal_session_id": "sid-1"})
    out_required = system.clear_seal(confirm=True, expected_seal_reason="", expected_seal_session_id="")
    assert out_required["status"] == "rejected"
    assert out_required["reason"] == "seal_identity_required"

    # identity mismatch
    out_mismatch = system.clear_seal(confirm=True, expected_seal_reason="other", expected_seal_session_id="sid-1")
    assert out_mismatch["status"] == "rejected"
    assert out_mismatch["reason"] == "seal_identity_mismatch"

    # success path
    monkeypatch.setattr(system._seal, "mode", lambda: "sealed")
    monkeypatch.setattr(system._seal, "clear_seal", lambda reason="": {"mode": "active"})
    _direct_gate(system, monkeypatch)
    out_ok = system.clear_seal(
        confirm=True,
        expected_seal_reason="r1",
        expected_seal_session_id="sid-1",
    )
    assert out_ok["status"] == "success"
    assert out_ok["operation_id"] == "op-1"


def test_replay_and_recover_blocked_or_partial(tmp_path: Path, monkeypatch) -> None:
    cfg = _config(tmp_path)
    cfg["memory_dir"].mkdir(parents=True, exist_ok=True)
    cfg["workspace_dir"].mkdir(parents=True, exist_ok=True)
    system = ShadowSystem(cfg)
    monkeypatch.setattr(system, "_stack_guard_ok", lambda: True)
    monkeypatch.setattr(system._seal, "mode", lambda: "sealed")
    _direct_gate(system, monkeypatch)

    # checkpoint blocked branch
    monkeypatch.setattr(system.checkpoint_guard, "verify_gate", lambda: {"ok": False, "reason": "bad_cp"})
    out_blocked = system.replay_spool()
    assert out_blocked["status"] == "success"
    assert out_blocked["result"]["status"] == "blocked"
    assert out_blocked["result"]["error"] == "bad_cp"

    # lease expired partial branch
    monkeypatch.setattr(system.checkpoint_guard, "verify_gate", lambda: {"ok": True})
    monkeypatch.setattr(system.recovery, "replay_spool", lambda target="main_memory": {"replayed": 1, "failed": 0, "skipped": 0, "remaining": 0})
    monkeypatch.setattr(system.locking, "validate_lease", lambda lease_id: False)
    out_partial = system.replay_spool()
    assert out_partial["status"] == "success"
    assert out_partial["result"]["status"] == "partial"
    assert out_partial["result"]["error"] == "lease_expired_midflight"

    # recover_from_events partial branch
    monkeypatch.setattr(
        system.recovery,
        "recover_from_events",
        lambda target="main_memory", since_ts=None: {"recovered": 1, "failed": 0, "skipped": 0, "quarantined": 0},
    )
    out_partial2 = system.recover_from_events()
    assert out_partial2["status"] == "success"
    assert out_partial2["result"]["status"] == "partial"
    assert out_partial2["result"]["error"] == "lease_expired_midflight"


def test_verify_archive_heal_rotate_and_snapshot_guards(tmp_path: Path, monkeypatch) -> None:
    cfg = _config(tmp_path)
    cfg["memory_dir"].mkdir(parents=True, exist_ok=True)
    cfg["workspace_dir"].mkdir(parents=True, exist_ok=True)
    system = ShadowSystem(cfg)

    # verify_checkpoints tamper path
    monkeypatch.setattr(system.ledger, "verify_checkpoints", lambda: {"ok": False, "reason": "tamper"})
    calls = {"sealed": 0}
    monkeypatch.setattr(system._seal, "trigger_seal", lambda reason="", level="hard": calls.__setitem__("sealed", calls["sealed"] + 1) or {"mode": "sealed"})
    monkeypatch.setattr(system.ledger, "append_verify_result", lambda out: None)
    out_verify = system.verify_checkpoints()
    assert out_verify["ok"] is False
    assert calls["sealed"] == 1

    monkeypatch.setattr(system.ledger, "archive_replayed_spool", lambda **kwargs: {"status": "success"})
    assert system.archive_replayed_spool()["status"] == "success"
    monkeypatch.setattr(system.ledger, "startup_self_heal", lambda: {"status": "success"})
    assert system.startup_self_heal()["status"] == "success"
    monkeypatch.setattr(system.ledger, "rotate_events_monthly", lambda: {"status": "success"})
    assert system.rotate_events_monthly()["status"] == "success"

    # whitelist helper
    snap_dir = system.backup_dir / "snapshot_20260523"
    snap_dir.mkdir(parents=True, exist_ok=True)
    good = snap_dir / "shadow_events.jsonl"
    bad = snap_dir / "not_allowed.txt"
    good.write_text("{}", encoding="utf-8")
    bad.write_text("x", encoding="utf-8")
    assert system._is_whitelisted_backup_snapshot(good) is True
    assert system._is_whitelisted_backup_snapshot(bad) is False


def test_sync_and_restore_backup_snapshot_branches(tmp_path: Path, monkeypatch) -> None:
    cfg = _config(tmp_path)
    cfg["memory_dir"].mkdir(parents=True, exist_ok=True)
    cfg["workspace_dir"].mkdir(parents=True, exist_ok=True)
    system = ShadowSystem(cfg)
    monkeypatch.setattr(system, "_stack_guard_ok", lambda: True)
    monkeypatch.setattr(system._seal, "mode", lambda: "sealed")
    _direct_gate(system, monkeypatch)

    # blocked when checkpoint/manifest not ok
    monkeypatch.setattr(system.checkpoint_guard, "verify_gate", lambda: {"ok": False})
    monkeypatch.setattr(system._seal, "status", lambda: {"manifest_signature_valid": True, "sealed_at": "2026-05-23T00:00:00Z", "last_recovered_at": ""})
    out_blocked = system.sync_verified_backup()
    assert out_blocked["status"] == "success"
    assert out_blocked["result"]["status"] == "blocked"

    # success sync path
    monkeypatch.setattr(system.checkpoint_guard, "verify_gate", lambda: {"ok": True})
    monkeypatch.setattr(system._seal, "status", lambda: {"manifest_signature_valid": True, "sealed_at": "2026-05-23T00:00:00Z", "last_recovered_at": ""})
    system._startup_manifest_untrusted = False
    # ensure source files exist
    system.ledger.events_file.parent.mkdir(parents=True, exist_ok=True)
    system.ledger.events_file.write_text("e\n", encoding="utf-8")
    system.ledger.checkpoints_file.write_text("c\n", encoding="utf-8")
    system._seal.manifest_file.write_text("m\n", encoding="utf-8")
    monkeypatch.setattr(system.ledger, "list_snapshots", lambda limit=10: [])
    out_sync = system.sync_verified_backup()
    assert out_sync["status"] == "success"
    assert out_sync["result"]["status"] == "success"
    assert "backup_dir" in out_sync["result"]

    # restore backup blocked: not whitelisted
    out_restore_blocked = system.restore_backup_snapshot(str(tmp_path / "outside.jsonl"))
    assert out_restore_blocked["status"] == "success"
    assert out_restore_blocked["result"]["status"] == "blocked"

    # restore backup blocked: invalid verify
    snap_dir = system.backup_dir / "snapshot_20260523"
    snap_dir.mkdir(parents=True, exist_ok=True)
    snap_file = snap_dir / "shadow_events.jsonl"
    snap_file.write_text("x\n", encoding="utf-8")
    monkeypatch.setattr(system.ledger, "verify_snapshot", lambda p: {"ok": False, "reason": "bad"})
    out_invalid = system.restore_backup_snapshot(str(snap_file))
    assert out_invalid["status"] == "success"
    assert out_invalid["result"]["status"] == "blocked"
    assert out_invalid["result"]["reason"] == "backup_snapshot_invalid"
