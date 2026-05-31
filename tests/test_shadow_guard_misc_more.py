from __future__ import annotations

import json
from pathlib import Path

from ms8.engine_core.security.shadow import shadow_guard as shadow_guard_mod
from ms8.engine_core.security.shadow.shadow_guard import ShadowSystem, get_shadow_system


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


def test_mark_startup_integrity_emit_file_written(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg["memory_dir"].mkdir(parents=True, exist_ok=True)
    cfg["workspace_dir"].mkdir(parents=True, exist_ok=True)
    sys = ShadowSystem(cfg)
    sys._mark_startup_integrity_emitted("ok")
    state = sys._startup_integrity_emit_state_file
    assert state.exists()
    payload = json.loads(state.read_text(encoding="utf-8"))
    assert payload["signature"] == "ok"
    assert "ts" in payload


def test_bind_target_and_req_and_usage(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg["memory_dir"].mkdir(parents=True, exist_ok=True)
    cfg["workspace_dir"].mkdir(parents=True, exist_ok=True)
    sys = ShadowSystem(cfg)

    sys.bind_recovery_target("t1", lambda text, source, meta: None, lambda h: False)  # noqa: ANN001
    assert sys._bound_targets.get("t1") is True

    req = sys._req("callerA", "reasonA")
    assert req.caller_id == "callerA"
    assert req.request_reason == "reasonA"
    assert isinstance(req.request_token, str)

    usage = sys._shadow_usage_bytes()
    assert isinstance(usage, dict)


def test_status_history_truncate_and_spool_fallback(tmp_path: Path, monkeypatch) -> None:
    cfg = _config(tmp_path)
    cfg["memory_dir"].mkdir(parents=True, exist_ok=True)
    cfg["workspace_dir"].mkdir(parents=True, exist_ok=True)
    sys = ShadowSystem(cfg)

    monkeypatch.setattr(
        sys._seal,
        "status",
        lambda: {
            "mode": "active",
            "sealed": False,
            "history": [{"id": 1}, {"id": 2}, {"id": 3}],
            "spool_pending_count": 9,
            "spool_oldest_pending_ts": "2026-01-01T00:00:00+00:00",
        },
    )
    monkeypatch.setattr(sys.ledger, "read_spool", lambda: (_ for _ in ()).throw(OSError("x")))
    st = sys.status(verbose=False, history_limit=1)
    assert st["manifest"]["history_count"] == 3
    assert len(st["manifest"]["history"]) == 1
    assert st["spool_pending"] == 9

    st2 = sys.status(verbose=False, history_limit=0)
    assert st2["manifest"]["history"] == []


def test_trigger_seal_and_clear_seal_legacy_session_paths(tmp_path: Path, monkeypatch) -> None:
    cfg = _config(tmp_path)
    cfg["memory_dir"].mkdir(parents=True, exist_ok=True)
    cfg["workspace_dir"].mkdir(parents=True, exist_ok=True)
    sys = ShadowSystem(cfg)
    monkeypatch.setattr(sys, "_stack_guard_ok", lambda: True)

    calls: list[dict] = []

    def _exec(**kwargs):
        calls.append(kwargs)
        return {"status": "success", "operation_id": "opx", "result": {"status": "success"}}

    monkeypatch.setattr(sys.gate, "execute", _exec)
    monkeypatch.setattr(sys._seal, "is_sealed", lambda: True)
    out_manual = sys.trigger_seal(reason="manual_test", level="hard", bypass_cooldown=False)
    assert out_manual["status"] == "success"
    assert calls[-1]["cooldown_s"] == 0

    out_bypass = sys.trigger_seal(reason="auto", level="soft", bypass_cooldown=True)
    assert out_bypass["status"] == "success"
    assert calls[-1]["cooldown_s"] == 0

    # legacy path: no seal_session_id in current seal state, only reason required
    monkeypatch.setattr(sys._seal, "status", lambda: {"reason": "r1", "seal_session_id": "", "mode": "sealed"})
    monkeypatch.setattr(sys._seal, "clear_seal", lambda reason="manual": {"mode": "active"})
    out = sys.clear_seal(confirm=True, expected_seal_reason="r1", expected_seal_session_id="")
    assert out["status"] == "success"


def test_health_check_nonreadonly_and_report_persist_error(tmp_path: Path, monkeypatch) -> None:
    cfg = _config(tmp_path)
    cfg["memory_dir"].mkdir(parents=True, exist_ok=True)
    cfg["workspace_dir"].mkdir(parents=True, exist_ok=True)
    sys = ShadowSystem(cfg)

    # non-readonly append failure branch + inconsistent state branch
    monkeypatch.setattr(sys.ledger, "append_event", lambda **kwargs: (_ for _ in ()).throw(OSError("append denied")))
    monkeypatch.setattr(sys._seal, "status", lambda: {"mode": "active", "sealed": True})

    orig_write_text = Path.write_text

    def _fail_report(self: Path, *args, **kwargs):
        if self.name == "shadow_health_report_latest.tmp":
            raise OSError("persist denied")
        return orig_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", _fail_report)
    out = sys.health_check(readonly=False)
    assert out["checks"]["events_appendable"] is False
    assert out["checks"]["state_consistent"] is False
    assert any("report_persist_error:" in str(x) for x in out["errors"])


def test_restore_backup_manifest_restore_fail_and_error_paths(tmp_path: Path, monkeypatch) -> None:
    cfg = _config(tmp_path)
    cfg["memory_dir"].mkdir(parents=True, exist_ok=True)
    cfg["workspace_dir"].mkdir(parents=True, exist_ok=True)
    sys = ShadowSystem(cfg)
    monkeypatch.setattr(sys, "_stack_guard_ok", lambda: True)
    monkeypatch.setattr(sys._seal, "mode", lambda: "sealed")

    monkeypatch.setattr(
        sys.gate,
        "execute",
        lambda **kwargs: {"status": "success", "operation_id": "op-1", "result": kwargs["callback"]("lease-1")},
    )
    monkeypatch.setattr(sys.ledger, "verify_snapshot", lambda p: {"ok": True, "path": p})
    monkeypatch.setattr(sys.ledger, "_read_last_seq", lambda: 1)
    monkeypatch.setattr(sys.ledger, "startup_self_heal", lambda: {"status": "ok"})

    snap_dir = sys.backup_dir / "snapshot_20260526"
    snap_dir.mkdir(parents=True, exist_ok=True)
    snap_file = snap_dir / "shadow_events.jsonl"
    snap_file.write_text("e\n", encoding="utf-8")
    (snap_dir / "shadow_checkpoints.jsonl").write_text("c\n", encoding="utf-8")
    (snap_dir / "seal_manifest.json").write_text("m\n", encoding="utf-8")

    monkeypatch.setattr(sys._seal, "restore_manifest_snapshot", lambda p: {"status": "error", "reason": "bad_mf"})
    out_fail = sys.restore_backup_snapshot(str(snap_file))
    assert out_fail["status"] == "success"
    assert out_fail["result"]["reason"] == "manifest_restore_failed"

    monkeypatch.setattr(
        sys._seal,
        "restore_manifest_snapshot",
        lambda p: (_ for _ in ()).throw(OSError("mf exception")),
    )
    out_err = sys.restore_backup_snapshot(str(snap_file))
    assert out_err["status"] == "success"
    assert "manifest_restore_error:" in out_err["result"]["reason"]


def test_search_shadow_and_get_shadow_system_fallback(tmp_path: Path, monkeypatch) -> None:
    cfg = _config(tmp_path)
    cfg["memory_dir"].mkdir(parents=True, exist_ok=True)
    cfg["workspace_dir"].mkdir(parents=True, exist_ok=True)
    sys = ShadowSystem(cfg)

    monkeypatch.setattr(
        sys.ledger,
        "read_events",
        lambda: iter(
            [
                {"event_type": "data", "action": "write", "summary": "hello world", "source": "s1", "ts": "t1"},
                {"event_type": "data", "action": "write", "summary": "hello again", "source": "s2", "ts": "t2"},
                {"event_type": "protection", "action": "protect", "summary": "x", "source": "p", "ts": "t3"},
            ]
        ),
    )
    out = sys.search_shadow("hello", limit=1)
    assert len(out) == 1
    assert out[0]["search_type"] == "shadow"

    # force singleton cache miss and constructor failure -> NullShadowSystem fallback
    shadow_guard_mod._SHADOW_SINGLETONS.clear()
    monkeypatch.setattr(shadow_guard_mod, "ShadowSystem", lambda _cfg: (_ for _ in ()).throw(OSError("boom")))
    null_inst = get_shadow_system(cfg)
    st = null_inst.status()
    assert st["enabled"] is False


def test_spool_encrypt_decrypt_and_nullshadow_helpers(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg["memory_dir"].mkdir(parents=True, exist_ok=True)
    cfg["workspace_dir"].mkdir(parents=True, exist_ok=True)
    sys = ShadowSystem(cfg)

    class _CryptoLocked:
        def is_enabled(self):
            return True

        def is_unlocked(self):
            return False

        def encrypt_before_write(self, data: bytes, file_type: str = "") -> bytes:
            return b"enc:" + data

        def decrypt_after_read(self, blob: bytes, allow_plaintext: bool = True) -> bytes:
            return blob

    class _CryptoReady:
        def is_enabled(self):
            return True

        def is_unlocked(self):
            return True

        def encrypt_before_write(self, data: bytes, file_type: str = "") -> bytes:
            return b"enc:" + data

        def decrypt_after_read(self, blob: bytes, allow_plaintext: bool = True) -> bytes:
            if blob.startswith(b"enc:"):
                return blob[4:]
            return blob

    # locked branch -> plaintext passthrough
    sys._crypto = _CryptoLocked()
    plain = sys._encrypt_spool_payload("hello")
    assert plain == "hello"

    # enabled+unlocked -> base64 ciphertext and successful decrypt
    sys._crypto = _CryptoReady()
    cipher = sys._encrypt_spool_payload("hello")
    assert cipher != "hello"
    assert sys._decrypt_spool_payload(cipher) == "hello"

    # decrypt invalid base64 -> fallback original text
    assert sys._decrypt_spool_payload("not-base64-###") == "not-base64-###"

    # null-shadow helper methods
    ns = shadow_guard_mod.NullShadowSystem("x")
    assert ns.is_enabled() is False
    assert ns.is_sealed() is False
    assert ns.should_takeover_write("high") is False
    assert ns.health_check()["enabled"] is False
    assert ns.search_shadow("abc", limit=2) == []
    assert ns.handle_write_success() is None
    assert ns.any_unknown_method()["status"] == "disabled"


def test_handle_write_error_and_record_mode_paths(tmp_path: Path, monkeypatch) -> None:
    cfg = _config(tmp_path)
    cfg["memory_dir"].mkdir(parents=True, exist_ok=True)
    cfg["workspace_dir"].mkdir(parents=True, exist_ok=True)
    sys = ShadowSystem(cfg)

    # Not sealed -> trigger_seal with configured level
    monkeypatch.setattr(sys._seal, "is_sealed", lambda: False)
    triggered: dict[str, str] = {}

    def _trigger(reason: str, level: str, caller_id: str = "", **kwargs):  # noqa: ANN001
        triggered["reason"] = reason
        triggered["level"] = level
        triggered["caller"] = caller_id
        return {"status": "success", "mode": "sealed"}

    monkeypatch.setattr(sys, "trigger_seal", _trigger)
    out = sys.handle_write_error("disk full")
    assert out["status"] == "success"
    assert triggered["reason"] == "disk full"
    assert triggered["caller"] == "system_bootstrap"

    # Already sealed + promoted_to_hard -> record_mode called with promotion metadata
    monkeypatch.setattr(sys._seal, "is_sealed", lambda: True)
    monkeypatch.setattr(
        sys._seal,
        "note_write_error",
        lambda threshold, reason: {"promoted_to_hard": True, "reason": reason},  # noqa: ARG005
    )
    seen: dict[str, dict] = {}

    def _record_mode(action: str, **kwargs):  # noqa: ANN001
        seen["action"] = action
        seen["kwargs"] = kwargs
        return {"ok": True}

    monkeypatch.setattr(sys, "record_mode", _record_mode)
    promoted = sys.handle_write_error("io error", source="shadow:test")
    assert promoted["promoted_to_hard"] is True
    assert seen["action"] == "seal"
    assert seen["kwargs"]["metadata"]["trigger"] == "soft_to_hard_promotion"


def test_record_data_sampling_and_replay_recover_blocked(tmp_path: Path, monkeypatch) -> None:
    cfg = _config(tmp_path)
    cfg["memory_dir"].mkdir(parents=True, exist_ok=True)
    cfg["workspace_dir"].mkdir(parents=True, exist_ok=True)
    sys = ShadowSystem(cfg)

    # minimal_survival read sampling: first skipped, second emitted
    monkeypatch.setattr(sys._seal, "mode", lambda: "minimal_survival")
    sys.minimal_survival_read_sample_every = 2
    emitted: list[dict] = []
    monkeypatch.setattr(
        sys.ledger,
        "append_event",
        lambda **kwargs: emitted.append(kwargs) or {"event_id": "e1"},
    )
    assert sys.record_data(action="read", source="s", content="a") == {}
    out = sys.record_data(action="read", source="s", content="b")
    assert out["event_id"] == "e1"
    assert len(emitted) == 1

    # stack guard blocked branches for replay / recover
    monkeypatch.setattr(sys, "_stack_guard_ok", lambda: False)
    blocked_replay = sys.replay_spool()
    blocked_recover = sys.recover_from_events()
    assert blocked_replay["reason"] == "stack_guard_blocked"
    assert blocked_recover["reason"] == "stack_guard_blocked"


def test_record_mode_emits_system_log_for_protected_actions(tmp_path: Path, monkeypatch) -> None:
    cfg = _config(tmp_path)
    cfg["memory_dir"].mkdir(parents=True, exist_ok=True)
    cfg["workspace_dir"].mkdir(parents=True, exist_ok=True)
    sys = ShadowSystem(cfg)
    monkeypatch.setattr(sys._seal, "mode", lambda: "sealed")
    monkeypatch.setattr(sys.ledger, "append_event", lambda **kwargs: {"event_id": "evt-1", **kwargs})

    emitted: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        shadow_guard_mod,
        "emit_system_log",
        lambda event, payload: emitted.append((event, payload)),
    )
    sys.record_mode("seal", source="shadow:test", ok=False, error="x")
    assert emitted
    name, payload = emitted[-1]
    assert name == "shadow_seal"
    assert payload["event_id"] == "evt-1"


def test_verify_archive_self_heal_rotate_and_recovery_drill_paths(tmp_path: Path, monkeypatch) -> None:
    cfg = _config(tmp_path)
    cfg["memory_dir"].mkdir(parents=True, exist_ok=True)
    cfg["workspace_dir"].mkdir(parents=True, exist_ok=True)
    sys = ShadowSystem(cfg)

    # verify_checkpoints: append_verify_result OSError branch should be tolerated
    monkeypatch.setattr(sys.ledger, "verify_checkpoints", lambda: {"ok": True, "status": "ok"})
    monkeypatch.setattr(
        sys.ledger,
        "append_verify_result",
        lambda _out: (_ for _ in ()).throw(OSError("append fail")),
    )
    verified = sys.verify_checkpoints()
    assert verified["ok"] is True

    monkeypatch.setattr(sys.ledger, "archive_replayed_spool", lambda **kwargs: {"status": "success", **kwargs})
    monkeypatch.setattr(sys.ledger, "startup_self_heal", lambda: {"status": "success"})
    monkeypatch.setattr(sys.ledger, "rotate_events_monthly", lambda: {"status": "success"})
    assert sys.archive_replayed_spool()["status"] == "success"
    assert sys.startup_self_heal()["status"] == "success"
    assert sys.rotate_events_monthly()["status"] == "success"

    # recovery drill blocked when already sealed
    monkeypatch.setattr(sys, "is_sealed", lambda: True)
    blocked = sys.run_recovery_drill()
    assert blocked["status"] == "blocked"
    assert blocked["reason"] == "sealed_already"

    # recovery drill rejected when trigger_seal fails
    monkeypatch.setattr(sys, "is_sealed", lambda: False)
    monkeypatch.setattr(sys, "trigger_seal", lambda *args, **kwargs: {"sealed": False, "status": "rejected"})
    rejected = sys.run_recovery_drill()
    assert rejected["status"] == "rejected"
    assert rejected["reason"] == "drill_seal_failed"


def test_replay_recover_checkpoint_blocked_and_minimal_survival_spool(tmp_path: Path, monkeypatch) -> None:
    cfg = _config(tmp_path)
    cfg["memory_dir"].mkdir(parents=True, exist_ok=True)
    cfg["workspace_dir"].mkdir(parents=True, exist_ok=True)
    sys = ShadowSystem(cfg)

    monkeypatch.setattr(sys, "_stack_guard_ok", lambda: True)
    monkeypatch.setattr(sys._seal, "mode", lambda: "sealed")
    monkeypatch.setattr(
        sys.gate,
        "execute",
        lambda **kwargs: {"status": "success", "operation_id": "op-1", "result": kwargs["callback"]("lease-1")},
    )
    monkeypatch.setattr(sys.checkpoint_guard, "verify_gate", lambda: {"ok": False, "reason": "checkpoint_mismatch"})

    replay_blocked = sys.replay_spool()
    recover_blocked = sys.recover_from_events()
    assert replay_blocked["status"] == "success"
    assert replay_blocked["result"]["status"] == "blocked"
    assert recover_blocked["result"]["status"] == "blocked"

    # minimal_survival spool path (record_mode protect branch)
    monkeypatch.setattr(sys._seal, "mode", lambda: "minimal_survival")
    monkeypatch.setattr(sys.ledger, "append_spool", lambda source, content: {"spool_id": "sp1", "source": source, "content": content})
    monkeypatch.setattr(sys._seal, "inc_sealed_writes", lambda _n: None)
    item = sys.spool_write("important content", source="shadow:test")
    assert item["minimal_survival"] is True
    assert item["content"] == "important content"[:200]


def test_sync_verified_backup_blocked_reasons(tmp_path: Path, monkeypatch) -> None:
    cfg = _config(tmp_path)
    cfg["memory_dir"].mkdir(parents=True, exist_ok=True)
    cfg["workspace_dir"].mkdir(parents=True, exist_ok=True)
    sys = ShadowSystem(cfg)

    monkeypatch.setattr(sys, "_stack_guard_ok", lambda: True)
    monkeypatch.setattr(
        sys.gate,
        "execute",
        lambda **kwargs: {"status": "success", "operation_id": "op-2", "result": kwargs["callback"]("lease-2")},
    )
    monkeypatch.setattr(sys._seal, "mode", lambda: "sealed")

    # checkpoint gate fail
    monkeypatch.setattr(sys.checkpoint_guard, "verify_gate", lambda: {"ok": False})
    monkeypatch.setattr(sys._seal, "status", lambda: {"manifest_signature_valid": True, "sealed_at": "2026-01-01T00:00:00+00:00"})
    out_chk = sys.sync_verified_backup()
    assert out_chk["status"] == "success"
    assert out_chk["result"]["status"] == "blocked"
    assert out_chk["result"]["checkpoint_ok"] is False

    # startup manifest untrusted fail
    monkeypatch.setattr(sys.checkpoint_guard, "verify_gate", lambda: {"ok": True})
    monkeypatch.setattr(sys._seal, "status", lambda: {"manifest_signature_valid": True, "sealed_at": "2026-01-01T00:00:00+00:00"})
    sys._startup_manifest_untrusted = True
    out_untrusted = sys.sync_verified_backup()
    assert out_untrusted["result"]["status"] == "blocked"
    assert out_untrusted["result"]["startup_manifest_untrusted"] is True


def test_nocrypto_and_seal_view_helpers() -> None:
    crypto = shadow_guard_mod._NoCrypto()
    assert crypto.is_enabled() is False
    assert crypto.is_unlocked() is False
    assert crypto.encrypt_before_write(b"abc", file_type="log") == b"abc"
    assert crypto.decrypt_after_read(b"xyz") == b"xyz"

    class _DummySeal:
        def status(self) -> dict[str, str]:
            return {"mode": "sealed"}

        def mode(self) -> str:
            return "sealed"

        def is_sealed(self) -> bool:
            return True

        def seal_level(self) -> str:
            return "hard"

    view = shadow_guard_mod._ShadowSealView(_DummySeal())
    assert view.status()["mode"] == "sealed"
    assert view.mode() == "sealed"
    assert view.is_sealed() is True
    assert view.seal_level() == "hard"


def test_init_handles_startup_self_heal_and_integrity_scan_errors(tmp_path: Path, monkeypatch, capsys) -> None:
    cfg = _config(tmp_path)
    cfg["memory_dir"].mkdir(parents=True, exist_ok=True)
    cfg["workspace_dir"].mkdir(parents=True, exist_ok=True)
    cfg["settings"]["memory"]["security"]["shadow"]["auto_self_heal_on_startup"] = True

    monkeypatch.setattr(shadow_guard_mod.ShadowLedger, "startup_self_heal", lambda self: (_ for _ in ()).throw(OSError("heal boom")))
    monkeypatch.setattr(
        shadow_guard_mod.ShadowSystem,
        "_startup_integrity_scan",
        lambda self: (_ for _ in ()).throw(OSError("scan boom")),
    )
    _ = ShadowSystem(cfg)
    out = capsys.readouterr().out
    assert "Startup self-heal failed" in out
    assert "Startup integrity scan failed" in out


def test_init_uses_relative_backup_and_nocrypto_fallback_without_workspace(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg["memory_dir"].mkdir(parents=True, exist_ok=True)
    cfg["settings"]["memory"]["security"]["shadow"]["backup_dir"] = "rel_backup"
    cfg.pop("workspace_dir", None)

    sys = ShadowSystem(cfg)
    assert sys.backup_dir == (cfg["memory_dir"] / "rel_backup").resolve()
    assert isinstance(sys._crypto, shadow_guard_mod._NoCrypto)
