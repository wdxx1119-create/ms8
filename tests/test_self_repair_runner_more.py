from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from ms8.engine_core.maintenance.self_repair import repair_runner as rr


@dataclass
class _Core:
    config: dict[str, str]
    _last_write_success_at: str = ""

    def shadow_status(self):  # noqa: D401
        return {"sealed": False}


def test_fingerprint_and_parse_iso_and_group_domain() -> None:
    fp = rr._fingerprint("a", "b", {"x": 1})
    assert isinstance(fp, str) and len(fp) == 40
    assert rr._parse_iso("bad") is None
    assert rr._parse_iso("2026-01-01T00:00:00+00:00") is not None

    grouped = rr._group_domain(
        [{"domain": "memory", "x": 1}, {"domain": "security", "x": 2}, {"x": 3}],
    )
    assert set(grouped.keys()) == {"memory", "security"}
    assert len(grouped["memory"]) == 2


def test_recently_executed_and_count_recent_attempts(tmp_path: Path) -> None:
    mem = tmp_path / "memory"
    logs = mem / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    old_iso = "2000-01-01T00:00:00+00:00"
    fp = "abc"
    rows = [
        {"timestamp": old_iso, "action_fingerprint": fp, "action": "x", "check_id": "c1", "mode": "apply"},
        {"timestamp": now_iso, "action_fingerprint": fp, "action": "x", "check_id": "c1", "mode": "apply"},
    ]
    (logs / "repair_ops_audit.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    assert rr._recently_executed(mem, fp, "x", within_seconds=999999) is True
    assert rr._count_recent_check_attempts(mem, "c1", hours=99999) >= 1

    # fallback maintenance_policy_log path by action
    (mem / "maintenance_policy_log.jsonl").write_text(
        json.dumps({"timestamp": now_iso, "action": "y"}) + "\n",
        encoding="utf-8",
    )
    assert rr._recently_executed(mem, "", "y", within_seconds=999999) is True


def test_lock_acquire_release_and_stale(tmp_path: Path) -> None:
    mem = tmp_path / "memory"
    ok, lock = rr._acquire_repair_lock(mem)
    assert ok is True
    assert Path(lock).exists()
    ok2, _lock2 = rr._acquire_repair_lock(mem)
    assert ok2 is False
    rr._release_repair_lock(lock)
    assert not Path(lock).exists()

    # stale lock branch
    stale = mem / "state" / "repair_in_progress.lock"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("x", encoding="utf-8")
    old = time.time() - (rr.REPAIR_LOCK_STALE_SECONDS + 10)
    os.utime(stale, (old, old))
    ok3, lock3 = rr._acquire_repair_lock(mem)
    assert ok3 is True
    rr._release_repair_lock(lock3)


def test_probe_helpers_and_runtime_health(tmp_path: Path) -> None:
    mem = tmp_path / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    f = mem / "auto_memory_records.jsonl"
    f.write_text("x", encoding="utf-8")

    probe = rr._file_probe(f)
    assert probe["exists"] is True
    before = rr._before_probe(mem, "repair_jsonl", "t", "c")
    assert "file" in before
    rb = rr._rollback_verify(mem, before, "repair_jsonl")
    assert rb["status"] in {"ok", "mismatch"}

    # runtime health reader
    rt = mem.parent / "runtime"
    rt.mkdir(parents=True, exist_ok=True)
    (rt / "health.json").write_text(json.dumps({"mcp_server": {"active_connections": 2}}), encoding="utf-8")
    assert rr._runtime_mcp_active_connections(mem) == 2


def test_root_cause_probe_and_rules_and_followups() -> None:
    core = _Core(config={"memory_dir": "/tmp"})
    row = {"check_id": "l1_disk_space", "action": "cleanup_disk"}
    out = rr._root_cause_probe(core, row, apply_result={"status": "error"}, verify_result={"status": "fail"})
    assert "hints" in out
    assert "apply_failed" in out["hints"]

    default_rules = rr._default_dynamic_chain_rules()
    assert len(default_rules) >= 1
    loaded = rr._load_dynamic_chain_rules({"dynamic_repair_chain": {"enabled": False}})
    assert loaded == []

    rows = []
    planned = set()
    appended = rr._append_followups(
        rows,
        {"action": "repair_jsonl", "check_id": "l2_jsonl_parse"},
        {"status": "ok"},
        {"status": "pass"},
        planned,
        default_rules,
    )
    assert len(appended) >= 1
    assert len(rows) >= 1


def test_repair_window_gate_and_notify() -> None:
    core = _Core(config={"memory_dir": "/tmp"})
    # no blocking in dry-run
    gate = rr._repair_window_gate(
        core,
        Path("/tmp"),
        {"repair_window": {"enabled": True}},
        {"auto": True},
        risk="R2",
        mode="dry-run",
    )
    assert gate["blocked"] is False

    n = rr._notify_repair_summary({"summary": {"failed": 0, "needs_manual": 0}, "executed": []})
    # On non-macos it's skipped; on macos with zero alerts also skipped.
    assert n["status"] in {"skipped", "ok", "error"}


def test_repair_window_gate_blocked_with_reasons_and_parse_fallbacks(tmp_path: Path) -> None:
    mem = tmp_path / "memory"
    mem.mkdir(parents=True, exist_ok=True)

    # Simulate active session and active MCP connections.
    runtime_dir = mem.parent / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "health.json").write_text(json.dumps({"mcp_server": {"active_connections": 5}}), encoding="utf-8")

    # touch a session activity probe file as "just now"
    marker = mem / "openclaw_session_ingest_state.json"
    marker.write_text("{}", encoding="utf-8")

    core = _Core(config={"memory_dir": str(mem)}, _last_write_success_at=time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()))
    gate = rr._repair_window_gate(
        core,
        mem,
        {
            "repair_window": {
                "enabled": True,
                "enforce_manual": True,
                "recent_write_seconds": "bad-int",
                "session_active_seconds": "bad-int",
                "mcp_active_connection_max": "bad-int",
            }
        },
        {"auto": False},
        risk="R2",
        mode="apply",
    )
    assert gate["blocked"] is True
    assert gate["error"] == "repair_window_busy"
    reasons = gate["details"]["reasons"]
    assert "recent_write_active" in reasons
    assert "session_activity_active" in reasons
    assert gate["details"]["recent_write_seconds"] == 300
    assert gate["details"]["session_active_seconds"] == 120
    assert gate["details"]["mcp_active_connection_max"] == 0


def test_repair_window_gate_not_blocked_for_r1_or_disabled(tmp_path: Path) -> None:
    mem = tmp_path / "memory"
    core = _Core(config={"memory_dir": str(mem)})
    r1 = rr._repair_window_gate(core, mem, {"repair_window": {"enabled": True}}, {"auto": True}, risk="R1", mode="apply")
    assert r1["blocked"] is False
    disabled = rr._repair_window_gate(
        core,
        mem,
        {"repair_window": {"enabled": False}},
        {"auto": True},
        risk="R3",
        mode="apply",
    )
    assert disabled["blocked"] is False


def test_run_repair_plan_lock_blocked_and_rollback_operation_paths(tmp_path: Path, monkeypatch) -> None:
    mem = tmp_path / "memory"
    core = _Core(config={"memory_dir": str(mem), "settings": {"memory": {"self_check": {}}}})

    monkeypatch.setattr(rr, "_acquire_repair_lock", lambda _m: (False, str(mem / "state" / "repair.lock")))
    blocked = rr.run_repair_plan(core, {"plan": []}, mode="apply")
    assert blocked["status"] == "blocked"
    assert blocked["error"] == "repair_in_progress"

    # rollback operation paths
    err_missing = rr.rollback_operation(core, "")
    assert err_missing["status"] == "error"

    err_audit = rr.rollback_operation(core, "op-x")
    assert err_audit["status"] == "error"

    logs = mem / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "repair_ops_audit.jsonl").write_text(
        json.dumps({"operation_id": "op-1", "action": "unknown", "check_id": "c1", "domain": "memory"}) + "\n",
        encoding="utf-8",
    )
    err_hooks = rr.rollback_operation(core, "op-1")
    assert err_hooks["status"] == "error"


def test_run_repair_plan_r3_blocked_without_approval(tmp_path: Path, monkeypatch) -> None:
    mem = tmp_path / "memory"
    core = _Core(config={"memory_dir": str(mem), "settings": {"memory": {"self_check": {}}}})

    monkeypatch.setattr(rr, "_acquire_repair_lock", lambda _m: (True, str(mem / "state" / "repair.lock")))
    monkeypatch.setattr(rr, "_release_repair_lock", lambda _p: None)
    monkeypatch.setattr(rr, "save_repair_report", lambda _m, _r: {"latest": "x", "history": "y"})
    monkeypatch.setattr(rr, "_notify_repair_summary", lambda _r: {"status": "skipped"})

    captured: list[dict] = []
    monkeypatch.setattr(rr, "append_repair_audit", lambda _m, row: captured.append(row.to_dict()))

    out = rr.run_repair_plan(
        core,
        {
            "plan": [
                {
                    "operation_id": "op-r3",
                    "check_id": "c-r3",
                    "action": "any",
                    "domain": "memory",
                    "risk": "R3",
                    "target": "t",
                }
            ],
            "r3_approved": False,
        },
        mode="apply",
    )
    assert out["status"] == "ok"
    assert out["summary"]["executed"] == 1
    assert out["executed"][0]["result"] == "blocked"
    assert "r3_requires_approval" in out["executed"][0]["error"]
    assert captured and captured[0]["result"] == "blocked"


def test_run_repair_plan_missing_hooks_and_precheck_blocked(tmp_path: Path, monkeypatch) -> None:
    mem = tmp_path / "memory"
    core = _Core(config={"memory_dir": str(mem), "settings": {"memory": {"self_check": {}}}})

    monkeypatch.setattr(rr, "_acquire_repair_lock", lambda _m: (True, str(mem / "state" / "repair.lock")))
    monkeypatch.setattr(rr, "_release_repair_lock", lambda _p: None)
    monkeypatch.setattr(rr, "save_repair_report", lambda _m, _r: {"latest": "x", "history": "y"})
    monkeypatch.setattr(rr, "_notify_repair_summary", lambda _r: {"status": "skipped"})
    monkeypatch.setattr(rr, "_recently_executed", lambda *_a, **_k: False)
    monkeypatch.setattr(rr, "_count_recent_check_attempts", lambda *_a, **_k: 0)
    monkeypatch.setattr(rr, "_repair_window_gate", lambda *_a, **_k: {"blocked": False})

    class _Hooks:
        @staticmethod
        def pre_check(_core, _row):
            return {"status": "blocked", "reason": "x"}

        @staticmethod
        def dry_run(_core, _row):
            return {"status": "ok"}

        @staticmethod
        def apply(_core, _row):
            return {"status": "ok"}

        @staticmethod
        def rollback(_core, _row):
            return {"status": "ok"}

    def _fake_get_hooks(action: str):
        if action == "nohooks":
            return None
        return _Hooks

    monkeypatch.setattr(rr, "get_hooks", _fake_get_hooks)
    out = rr.run_repair_plan(
        core,
        {
            "plan": [
                {
                    "operation_id": "op-1",
                    "check_id": "c-1",
                    "action": "nohooks",
                    "domain": "memory",
                    "risk": "R1",
                    "target": "t1",
                },
                {
                    "operation_id": "op-2",
                    "check_id": "c-2",
                    "action": "withhooks",
                    "domain": "memory",
                    "risk": "R1",
                    "target": "t2",
                },
            ]
        },
        mode="apply",
    )
    assert out["status"] == "ok"
    assert out["summary"]["executed"] == 2
    assert out["executed"][0]["error"] == "missing_policy_hooks"
    assert out["executed"][1]["result"] == "blocked"
    assert "pre_check:" in out["executed"][1]["error"]


def test_run_repair_plan_dry_run_path(tmp_path: Path, monkeypatch) -> None:
    mem = tmp_path / "memory"
    core = _Core(config={"memory_dir": str(mem), "settings": {"memory": {"self_check": {}}}})

    monkeypatch.setattr(rr, "_acquire_repair_lock", lambda _m: (True, str(mem / "state" / "repair.lock")))
    monkeypatch.setattr(rr, "_release_repair_lock", lambda _p: None)
    monkeypatch.setattr(rr, "save_repair_report", lambda _m, _r: {"latest": "x", "history": "y"})
    monkeypatch.setattr(rr, "_notify_repair_summary", lambda _r: {"status": "skipped"})
    monkeypatch.setattr(rr, "_recently_executed", lambda *_a, **_k: False)

    class _Hooks:
        @staticmethod
        def pre_check(_core, _row):
            return {"status": "ok"}

        @staticmethod
        def dry_run(_core, _row):
            return {"status": "ok", "impact": "none"}

        @staticmethod
        def apply(_core, _row):
            raise AssertionError("apply should not run in dry-run")

        @staticmethod
        def rollback(_core, _row):
            return {"status": "ok"}

    monkeypatch.setattr(rr, "get_hooks", lambda _a: _Hooks)
    monkeypatch.setattr(rr, "_root_cause_probe", lambda *_a, **_k: {"hints": ["x"]})
    out = rr.run_repair_plan(
        core,
        {
            "plan": [
                {
                    "operation_id": "op-dry",
                    "check_id": "c-dry",
                    "action": "dry_action",
                    "domain": "memory",
                    "risk": "R1",
                    "target": "t",
                }
            ]
        },
        mode="dry-run",
    )
    assert out["status"] == "ok"
    assert out["executed"][0]["result"] == "dry_run"
    assert out["executed"][0]["verify_status"] == "skipped"


def test_run_repair_plan_sealed_dedup_and_rate_limit(tmp_path: Path, monkeypatch) -> None:
    mem = tmp_path / "memory"

    class _SealedCore:
        config = {"memory_dir": str(mem), "settings": {"memory": {"self_check": {"self_repair_max_per_check_24h": 1}}}}
        _last_write_success_at = ""

        @staticmethod
        def shadow_status():
            return {"sealed": True}

    core = _SealedCore()
    monkeypatch.setattr(rr, "_acquire_repair_lock", lambda _m: (True, str(mem / "state" / "repair.lock")))
    monkeypatch.setattr(rr, "_release_repair_lock", lambda _p: None)
    monkeypatch.setattr(rr, "save_repair_report", lambda _m, _r: {"latest": "x", "history": "y"})
    monkeypatch.setattr(rr, "_notify_repair_summary", lambda _r: {"status": "skipped"})
    monkeypatch.setattr(rr, "_repair_window_gate", lambda *_a, **_k: {"blocked": False})

    # 1) sealed blocks non-whitelisted action
    monkeypatch.setattr(rr, "_recently_executed", lambda *_a, **_k: False)
    out_sealed = rr.run_repair_plan(
        core,
        {"plan": [{"operation_id": "op-s", "check_id": "c-s", "action": "rebuild_index", "domain": "memory", "risk": "R1", "target": "t"}]},
        mode="apply",
    )
    assert out_sealed["executed"][0]["result"] == "blocked"
    assert out_sealed["executed"][0]["error"] == "blocked_by_shadow_sealed"

    # 2) dedup recent action branch (use whitelisted action to pass sealed gate)
    monkeypatch.setattr(rr, "_recently_executed", lambda *_a, **_k: True)
    out_dup = rr.run_repair_plan(
        core,
        {"plan": [{"operation_id": "op-d", "check_id": "c-d", "action": "shadow_self_heal", "domain": "security", "risk": "R1", "target": "t"}]},
        mode="apply",
    )
    assert out_dup["executed"][0]["result"] == "skipped"
    assert out_dup["executed"][0]["error"] == "dedup_recent_action"

    # 3) rate limit branch
    monkeypatch.setattr(rr, "_recently_executed", lambda *_a, **_k: False)
    monkeypatch.setattr(rr, "_count_recent_check_attempts", lambda *_a, **_k: 5)

    class _Hooks:
        @staticmethod
        def pre_check(_core, _row):
            return {"status": "ok"}

        @staticmethod
        def dry_run(_core, _row):
            return {"status": "ok"}

        @staticmethod
        def apply(_core, _row):
            return {"status": "ok"}

        @staticmethod
        def rollback(_core, _row):
            return {"status": "ok"}

    monkeypatch.setattr(rr, "get_hooks", lambda _a: _Hooks)
    out_rate = rr.run_repair_plan(
        core,
        {"plan": [{"operation_id": "op-r", "check_id": "c-r", "action": "shadow_self_heal", "domain": "security", "risk": "R1", "target": "t"}]},
        mode="apply",
    )
    assert out_rate["executed"][0]["result"] == "blocked"
    assert out_rate["executed"][0]["error"] == "rate_limited_24h"


def test_run_repair_plan_apply_verify_fail_and_exception(tmp_path: Path, monkeypatch) -> None:
    mem = tmp_path / "memory"
    core = _Core(config={"memory_dir": str(mem), "settings": {"memory": {"self_check": {}}}})

    monkeypatch.setattr(rr, "_acquire_repair_lock", lambda _m: (True, str(mem / "state" / "repair.lock")))
    monkeypatch.setattr(rr, "_release_repair_lock", lambda _p: None)
    monkeypatch.setattr(rr, "save_repair_report", lambda _m, _r: {"latest": "x", "history": "y"})
    monkeypatch.setattr(rr, "_notify_repair_summary", lambda _r: {"status": "skipped"})
    monkeypatch.setattr(rr, "_recently_executed", lambda *_a, **_k: False)
    monkeypatch.setattr(rr, "_count_recent_check_attempts", lambda *_a, **_k: 0)
    monkeypatch.setattr(rr, "_repair_window_gate", lambda *_a, **_k: {"blocked": False})
    monkeypatch.setattr(rr, "verify_repair", lambda *_a, **_k: {"ok": False, "status": "fail"})
    monkeypatch.setattr(rr, "_root_cause_probe", lambda *_a, **_k: {"hints": ["verify_failed"]})
    monkeypatch.setattr(rr, "_rollback_verify", lambda *_a, **_k: {"status": "ok"})
    monkeypatch.setattr(rr, "_append_followups", lambda *_a, **_k: [])

    class _HooksFailVerify:
        @staticmethod
        def pre_check(_core, _row):
            return {"status": "ok"}

        @staticmethod
        def dry_run(_core, _row):
            return {"status": "ok"}

        @staticmethod
        def apply(_core, _row):
            return {"status": "ok"}

        @staticmethod
        def rollback(_core, _row):
            return {"status": "ok"}

    monkeypatch.setattr(rr, "get_hooks", lambda _a: _HooksFailVerify)
    out_fail_verify = rr.run_repair_plan(
        core,
        {"plan": [{"operation_id": "op-fv", "check_id": "c-fv", "action": "repair_jsonl", "domain": "memory", "risk": "R2", "target": "t"}]},
        mode="apply",
    )
    assert out_fail_verify["executed"][0]["result"] == "failed_verify"
    assert out_fail_verify["executed"][0]["rolled_back"] is True
    assert out_fail_verify["summary"]["failed"] >= 1

    class _HooksRaise:
        @staticmethod
        def pre_check(_core, _row):
            return {"status": "ok"}

        @staticmethod
        def dry_run(_core, _row):
            return {"status": "ok"}

        @staticmethod
        def apply(_core, _row):
            raise OSError("apply boom")

        @staticmethod
        def rollback(_core, _row):
            return {"status": "ok"}

    monkeypatch.setattr(rr, "get_hooks", lambda _a: _HooksRaise)
    out_exc = rr.run_repair_plan(
        core,
        {"plan": [{"operation_id": "op-ex", "check_id": "c-ex", "action": "repair_jsonl", "domain": "memory", "risk": "R2", "target": "t"}]},
        mode="apply",
    )
    assert out_exc["executed"][0]["result"] == "error"
    assert "apply boom" in out_exc["executed"][0]["error"]
    assert out_exc["executed"][0]["rolled_back"] is True


def test_run_repair_plan_shadow_status_oserror_branch(tmp_path: Path, monkeypatch) -> None:
    mem = tmp_path / "memory"

    class _CoreShadowErr:
        config = {"memory_dir": str(mem), "settings": {"memory": {"self_check": {}}}}
        _last_write_success_at = ""

        @staticmethod
        def shadow_status():
            raise OSError("no shadow")

    core = _CoreShadowErr()
    monkeypatch.setattr(rr, "_acquire_repair_lock", lambda _m: (True, str(mem / "state" / "repair.lock")))
    monkeypatch.setattr(rr, "_release_repair_lock", lambda _p: None)
    monkeypatch.setattr(rr, "save_repair_report", lambda _m, _r: {"latest": "x", "history": "y"})
    monkeypatch.setattr(rr, "_notify_repair_summary", lambda _r: {"status": "skipped"})
    monkeypatch.setattr(rr, "_recently_executed", lambda *_a, **_k: False)
    monkeypatch.setattr(rr, "_count_recent_check_attempts", lambda *_a, **_k: 0)
    monkeypatch.setattr(rr, "_repair_window_gate", lambda *_a, **_k: {"blocked": False})

    class _Hooks:
        @staticmethod
        def pre_check(_core, _row):
            return {"status": "ok"}

        @staticmethod
        def dry_run(_core, _row):
            return {"status": "ok"}

        @staticmethod
        def apply(_core, _row):
            return {"status": "ok"}

        @staticmethod
        def rollback(_core, _row):
            return {"status": "ok"}

    monkeypatch.setattr(rr, "get_hooks", lambda _a: _Hooks)
    monkeypatch.setattr(rr, "verify_repair", lambda *_a, **_k: {"ok": True, "status": "pass"})
    monkeypatch.setattr(rr, "_append_followups", lambda *_a, **_k: [])
    out = rr.run_repair_plan(
        core,
        {"plan": [{"operation_id": "op-shadow", "check_id": "c-shadow", "action": "repair_jsonl", "domain": "memory", "risk": "R2", "target": "t"}]},
        mode="apply",
    )
    assert out["status"] == "ok"
    assert out["summary"]["success"] == 1


def test_rollback_operation_success_and_exception(tmp_path: Path, monkeypatch) -> None:
    mem = tmp_path / "memory"
    logs = mem / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    core = _Core(config={"memory_dir": str(mem)})

    (logs / "repair_ops_audit.jsonl").write_text(
        json.dumps({"operation_id": "op-ok", "action": "any", "check_id": "c1", "domain": "memory", "target": "t"})
        + "\n",
        encoding="utf-8",
    )

    class _HooksOk:
        @staticmethod
        def rollback(_core, _row):
            return {"status": "ok"}

    monkeypatch.setattr(rr, "get_hooks", lambda _a: _HooksOk)
    monkeypatch.setattr(rr, "append_repair_audit", lambda *_a, **_k: None)
    ok = rr.rollback_operation(core, "op-ok")
    assert ok["status"] == "ok"
    assert ok["rollback_operation_id"] == "op-ok-rollback"

    class _HooksErr:
        @staticmethod
        def rollback(_core, _row):
            raise OSError("rollback boom")

    monkeypatch.setattr(rr, "get_hooks", lambda _a: _HooksErr)
    err = rr.rollback_operation(core, "op-ok")
    assert err["status"] == "error"
    assert "rollback boom" in err["error"]


def test_rollback_operation_not_found_with_bad_json_lines(tmp_path: Path) -> None:
    mem = tmp_path / "memory"
    logs = mem / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    core = _Core(config={"memory_dir": str(mem)})

    (logs / "repair_ops_audit.jsonl").write_text(
        '{"broken":\n'
        + json.dumps({"operation_id": "other-op", "action": "a", "check_id": "c1"})
        + "\n",
        encoding="utf-8",
    )
    out = rr.rollback_operation(core, "missing-op")
    assert out["status"] == "error"
    assert out["error"] == "operation_not_found"
