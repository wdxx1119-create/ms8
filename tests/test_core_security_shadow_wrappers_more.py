from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import ms8.engine_core.core as core_mod
from ms8.engine_core.core import MemoryCore
from ms8.engine_core.security.shadow import content_hash


class _DummyCrypto:
    def __init__(self, *, enabled: bool = False, unlocked: bool = True):
        self._enabled = enabled
        self._unlocked = unlocked
        self.lock_called = False

    def status(self):
        return {"enabled": self._enabled, "unlocked": self._unlocked}

    def enable_encryption(self, _pw: str):
        return {"status": "enabled"}

    def disable_encryption(self, _pw: str):
        return {"status": "disabled"}

    def unlock(self, _pw: str):
        self._unlocked = True
        return True

    def lock(self):
        self.lock_called = True
        self._unlocked = False

    def is_enabled(self):
        return self._enabled

    def is_unlocked(self):
        return self._unlocked


class _DummyShadow:
    def __init__(self):
        self.bound = []

    def status(self):
        return {"enabled": True}

    def issue_capability_token(self, **kwargs):
        self.last_issue = kwargs
        return "tok-1"

    def revoke_capability_token(self, token: str):
        return {"status": "success", "token": token}

    def trigger_seal(self, **kwargs):
        return {"status": "sealed", **kwargs}

    def clear_seal(self, **kwargs):
        return {"status": "unsealed", **kwargs}

    def health_check(self):
        return {"status": "ok"}

    def bind_recovery_target(self, target: str, write_func, exists_func):
        self.bound.append((target, write_func, exists_func))

    def replay_spool(self, **kwargs):
        return {"status": "success", "op": "replay", **kwargs}

    def archive_replayed_spool(self):
        return {"status": "success", "op": "archive"}

    def startup_self_heal(self):
        return {"status": "success", "op": "heal"}

    def rotate_events_monthly(self):
        return {"status": "success", "op": "rotate"}

    def sync_verified_backup(self, **kwargs):
        return {"status": "success", "op": "sync", **kwargs}

    def recover_from_events(self, **kwargs):
        return {"status": "success", "op": "recover", **kwargs}

    def verify_checkpoints(self):
        return {"status": "success", "op": "verify"}

    def reset_checkpoint(self):
        raise RuntimeError("checkpoint reset failed")

    def restore_shadow_snapshot(self, path: str, **kwargs):
        return {"status": "success", "path": path, **kwargs}

    def list_manifest_snapshots(self, limit: int = 20):
        return [{"path": "a", "limit": limit}]

    def restore_manifest_snapshot(self, path: str, **kwargs):
        return {"status": "success", "path": path, **kwargs}

    def restore_backup_snapshot(self, path: str, **kwargs):
        return {"status": "success", "path": path, **kwargs}

    def run_recovery_drill(self, **kwargs):
        return {"status": "success", "op": "drill", **kwargs}


def _core_with_security(tmp_path: Path) -> MemoryCore:
    core = MemoryCore.__new__(MemoryCore)
    core.config = {"memory_dir": tmp_path}
    core.crypto = _DummyCrypto(enabled=False, unlocked=True)
    core.shadow = _DummyShadow()
    core.file_store = SimpleNamespace(
        read_memory_md=lambda: "ROOT",
        write_memory_md=lambda _v: None,
        append_to_daily_log=lambda _v: None,
    )
    core.whoosh_search = SimpleNamespace(reindex_all=lambda: None)
    core._safe_text_for_memory_md = lambda text: {"allowed": True, "text": text}
    return core


def test_security_wrapper_methods(tmp_path: Path):
    core = _core_with_security(tmp_path)
    assert core.security_status()["enabled"] is False
    assert core.security_enable("pw")["status"] == "enabled"
    assert core.security_disable("pw")["status"] == "disabled"
    unlock = core.security_unlock("pw")
    assert unlock["status"] == "success"
    lock = core.security_lock()
    assert lock["status"] == "success"
    assert core.crypto.lock_called is True


def test_security_recover_wrapper_forwards_to_recovery_helper(tmp_path: Path, monkeypatch):
    core = _core_with_security(tmp_path)

    def _fake_recover(crypto, recovery_key: str, new_master_password: str):
        assert crypto is core.crypto
        assert recovery_key == "rk"
        assert new_master_password == "new-pass"
        return {"status": "success", "source": "wrapper"}

    monkeypatch.setattr(core_mod, "recover_with_recovery_key", _fake_recover)
    out = core.security_recover("rk", "new-pass")
    assert out["status"] == "success"
    assert out["source"] == "wrapper"


def test_shadow_disabled_shortcuts(tmp_path: Path):
    core = _core_with_security(tmp_path)
    core.shadow = None
    assert core.shadow_status() == {"enabled": False}
    assert core.shadow_issue_token("c", ["read"]) == {"status": "disabled"}
    assert core.shadow_replay_spool()["status"] == "disabled"


def test_shadow_issue_and_seal_paths(tmp_path: Path):
    core = _core_with_security(tmp_path)
    assert core.shadow_status()["enabled"] is True
    bad = core.shadow_issue_token("caller", [])
    assert bad["status"] == "error"
    ok = core.shadow_issue_token("caller", [" read ", "write"], ttl_seconds=1)
    assert ok["status"] == "success"
    assert ok["ttl_seconds"] == 30
    assert core.shadow_revoke_token("tok-1")["status"] == "success"
    assert core.shadow_seal(reason="r")["status"] == "sealed"
    assert core.shadow_unseal(reason="u")["status"] == "unsealed"
    assert core.shadow_health()["status"] == "ok"


def test_shadow_replay_blocked_when_encryption_locked(tmp_path: Path):
    core = _core_with_security(tmp_path)
    core.crypto = _DummyCrypto(enabled=True, unlocked=False)
    out = core.shadow_replay_spool()
    assert out["status"] == "blocked"
    assert out["reason"] == "memory_security_locked"


def test_shadow_replay_and_recover_bind_target(tmp_path: Path):
    core = _core_with_security(tmp_path)
    replay = core.shadow_replay_spool(caller_id="a", request_token="b")
    assert replay["status"] == "success"
    assert core.shadow.bound and core.shadow.bound[0][0] == "main_memory"
    recover = core.shadow_recover_from_events("2026-01-01")
    assert recover["status"] == "success"
    assert recover["op"] == "recover"


def test_shadow_misc_wrapper_calls_and_reset_error(tmp_path: Path):
    core = _core_with_security(tmp_path)
    assert core.shadow_archive_spool()["op"] == "archive"
    assert core.shadow_startup_self_heal()["op"] == "heal"
    assert core.shadow_rotate_events_monthly()["op"] == "rotate"
    assert core.shadow_sync_verified_backup()["op"] == "sync"
    assert core.shadow_verify()["op"] == "verify"
    reset = core.shadow_reset_checkpoint()
    assert reset["status"] == "error"
    assert "failed" in reset["error"]
    assert core.shadow_restore_snapshot("x")["status"] == "success"
    listed = core.shadow_list_manifest_snapshots(limit=3)
    assert listed["status"] == "success"
    assert listed["items"][0]["limit"] == 3
    assert core.shadow_restore_manifest_snapshot("m")["status"] == "success"
    assert core.shadow_restore_backup_snapshot("b")["status"] == "success"
    assert core.shadow_recovery_drill()["op"] == "drill"


def test_shadow_write_func_branches_and_reindex_error(tmp_path: Path):
    core = _core_with_security(tmp_path)
    writes: list[str] = []
    logs: list[str] = []
    core.file_store = SimpleNamespace(
        read_memory_md=lambda: "ROOT",
        write_memory_md=lambda v: writes.append(v),
        append_to_daily_log=lambda v: logs.append(v),
    )
    core._utc_now = lambda: __import__("datetime").datetime(2026, 5, 25)  # type: ignore[method-assign]

    # branch: source startswith core.save -> write memory.md
    core._safe_text_for_memory_md = lambda text: {"allowed": True, "text": text}  # type: ignore[method-assign]
    core.whoosh_search = SimpleNamespace(reindex_all=lambda: None)
    core._shadow_write_func("hello", "core.save.auto", {"trust_level": "hard"})
    assert writes and "Shadow Replay" in writes[-1]
    assert "[hard]" in writes[-1]

    # branch: not core.save -> append daily log
    core._shadow_write_func("world", "other.source", {"trust_level": "soft"})
    assert logs and logs[-1].startswith("[soft] ")

    # branch: safe filter denies write
    writes.clear()
    core._safe_text_for_memory_md = lambda text: {"allowed": False, "text": text}  # type: ignore[method-assign]
    core._shadow_write_func("blocked", "core.save.auto", {"trust_level": "hard"})
    assert writes == []

    # reindex failure is swallowed
    core._safe_text_for_memory_md = lambda text: {"allowed": True, "text": text}  # type: ignore[method-assign]
    core.whoosh_search = SimpleNamespace(reindex_all=lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    core._shadow_write_func("ok", "other.source", {"trust_level": "soft"})


def test_shadow_hash_exists_in_main_variants(tmp_path: Path):
    core = _core_with_security(tmp_path)
    mem_dir = core.config["memory_dir"]
    records = mem_dir / "auto_memory_records.jsonl"
    mem_dir.mkdir(parents=True, exist_ok=True)

    # empty hash
    assert core._shadow_hash_exists_in_main("") is False

    # parse bad row then good row with match
    target_text = "remember this"
    h = content_hash(target_text)
    records.write_text("{bad}\n" + '{"normalized_text":"x"}\n' + '{"text":"remember this"}\n', encoding="utf-8")
    assert core._shadow_hash_exists_in_main(h) is True

    # no match path
    assert core._shadow_hash_exists_in_main(content_hash("not-here")) is False
