from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ms8.engine_core.security.shadow.shadow_guard as sg
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


def test_startup_signature_and_emit_cooldown(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg["memory_dir"].mkdir(parents=True, exist_ok=True)
    cfg["workspace_dir"].mkdir(parents=True, exist_ok=True)
    system = ShadowSystem(cfg)

    assert system._startup_integrity_signature([]) == "ok"
    assert system._startup_integrity_signature(["b", "a", "a"]) == "fail:a|b"

    state = system._startup_integrity_emit_state_file
    if state.exists():
        state.unlink()
    # Missing state file -> emit.
    assert system._should_emit_startup_integrity("ok") is True

    # Same signature and recent timestamp -> suppress emit.
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(
        json.dumps({"signature": "ok", "ts": datetime.now(timezone.utc).isoformat()}),
        encoding="utf-8",
    )
    assert system._should_emit_startup_integrity("ok") is False

    # Signature change -> emit.
    assert system._should_emit_startup_integrity("fail:x") is True

    # Stale timestamp -> emit.
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    state.write_text(json.dumps({"signature": "ok", "ts": old_ts}), encoding="utf-8")
    assert system._should_emit_startup_integrity("ok") is True

    # Corrupt state -> emit.
    state.write_text("{", encoding="utf-8")
    assert system._should_emit_startup_integrity("ok") is True


def test_startup_integrity_scan_failure_and_success_paths(tmp_path: Path, monkeypatch) -> None:
    cfg = _config(tmp_path)
    cfg["memory_dir"].mkdir(parents=True, exist_ok=True)
    cfg["workspace_dir"].mkdir(parents=True, exist_ok=True)
    system = ShadowSystem(cfg)

    emitted: list[tuple[str, dict]] = []
    monkeypatch.setattr(sg, "emit_system_log", lambda name, payload: emitted.append((name, payload)))

    # Force failed gate + bad signature -> should find issues.
    monkeypatch.setattr(system.checkpoint_guard, "verify_gate", lambda: {"ok": False, "reason": "checkpoint_failed"})
    monkeypatch.setattr(system._seal, "status", lambda: {"manifest_signature_valid": False, "sealed": False})
    monkeypatch.setattr(system._seal, "trigger_seal", lambda reason="", level="hard": {"mode": "sealed", "reason": reason, "level": level})

    out_fail = system._startup_integrity_scan()
    assert out_fail["ok"] is False
    assert "checkpoint_failed" in out_fail["findings"]
    assert "manifest_signature_invalid" in out_fail["findings"]
    assert emitted

    # Healthy path should return ok and no findings.
    monkeypatch.setattr(system.checkpoint_guard, "verify_gate", lambda: {"ok": True})
    monkeypatch.setattr(system._seal, "status", lambda: {"manifest_signature_valid": True, "sealed": False})
    out_ok = system._startup_integrity_scan()
    assert out_ok["ok"] is True
    assert out_ok["findings"] == []


def test_checkpoint_reset_success_and_error(tmp_path: Path, monkeypatch) -> None:
    cfg = _config(tmp_path)
    cfg["memory_dir"].mkdir(parents=True, exist_ok=True)
    cfg["workspace_dir"].mkdir(parents=True, exist_ok=True)
    system = ShadowSystem(cfg)

    monkeypatch.setattr(system.ledger, "rebuild_checkpoints_from_events", lambda: {"rebuilt": 1})
    monkeypatch.setattr(system.checkpoint_guard, "verify_gate", lambda: {"ok": True})
    out_ok = system.reset_checkpoint()
    assert out_ok["status"] == "success"

    def _boom():
        raise OSError("rebuild failed")

    monkeypatch.setattr(system.ledger, "rebuild_checkpoints_from_events", _boom)
    out_err = system.reset_checkpoint()
    assert out_err["status"] == "error"
    assert "checkpoint_reset_failed" in out_err["reason"]


def test_startup_integrity_checkpoint_mismatch_rebase_and_seal_error(tmp_path: Path, monkeypatch) -> None:
    cfg = _config(tmp_path)
    cfg["memory_dir"].mkdir(parents=True, exist_ok=True)
    cfg["workspace_dir"].mkdir(parents=True, exist_ok=True)
    system = ShadowSystem(cfg)

    # mismatch -> rebase success path
    calls = {"verify": 0}

    def _verify_gate():
        calls["verify"] += 1
        if calls["verify"] == 1:
            return {"ok": False, "reason": "checkpoint_mismatch"}
        return {"ok": True}

    monkeypatch.setattr(system.checkpoint_guard, "verify_gate", _verify_gate)
    monkeypatch.setattr(system.ledger, "rebuild_checkpoints_from_events", lambda: {"rebuilt": 1})
    monkeypatch.setattr(system._seal, "status", lambda: {"manifest_signature_valid": True, "sealed": False})
    out = system._startup_integrity_scan()
    assert out["ok"] is True
    assert out["findings"] == []

    # findings branch with trigger_seal OSError should still return fail report
    monkeypatch.setattr(system.checkpoint_guard, "verify_gate", lambda: {"ok": False, "reason": "checkpoint_failed"})
    monkeypatch.setattr(system._seal, "status", lambda: {"manifest_signature_valid": True, "sealed": False, "reason": ""})
    monkeypatch.setattr(system._seal, "trigger_seal", lambda reason="", level="hard": (_ for _ in ()).throw(OSError("seal failed")))
    out2 = system._startup_integrity_scan()
    assert out2["ok"] is False
    assert "checkpoint_failed" in out2["findings"]


def test_startup_integrity_emit_mark_write_and_chmod_errors(tmp_path: Path, monkeypatch) -> None:
    cfg = _config(tmp_path)
    cfg["memory_dir"].mkdir(parents=True, exist_ok=True)
    cfg["workspace_dir"].mkdir(parents=True, exist_ok=True)
    system = ShadowSystem(cfg)

    # chmod failure branch should not raise
    monkeypatch.setattr(sg.os, "chmod", lambda *_a, **_k: (_ for _ in ()).throw(OSError("chmod denied")))
    system._mark_startup_integrity_emitted("ok")

    # write failure branch should not raise
    monkeypatch.setattr(
        Path,
        "write_text",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("write denied")),
    )
    system._mark_startup_integrity_emitted("ok")
