from __future__ import annotations

import asyncio
import collections
from types import SimpleNamespace
from typing import Any

from ms8.engine_core import core as core_mod
from ms8.engine_core.core import MemoryCore


def _core_stub() -> MemoryCore:
    c = MemoryCore.__new__(MemoryCore)
    c._recent_query_tokens = collections.deque(maxlen=24)
    c.config = {
        "workspace_dir": ".",
        "memory_dir": ".",
        "settings": {
            "memory": {
                "self_check": {"allow_r3_auto_apply": False},
                "maintenance_policy": {"enabled": True, "cooldown_hours": {"cleanup_test_memories": 24}},
            }
        },
    }
    c.crypto = SimpleNamespace(is_enabled=lambda: False, is_unlocked=lambda: True)
    return c


def test_run_async_plain_and_coroutine() -> None:
    c = _core_stub()
    assert c._run_async(123) == 123

    async def _coro() -> int:
        return 7

    assert c._run_async(_coro()) == 7


def test_run_self_check_error_wrapper(monkeypatch) -> None:
    c = _core_stub()

    def _boom(_self: Any, level: str = "L1") -> dict[str, Any]:
        raise RuntimeError("x")

    monkeypatch.setattr(core_mod, "run_self_check", _boom)
    out = c.run_self_check(level="L4")
    assert out["status"] == "error"
    assert out["level"] == "L4"


def test_run_self_repair_mode_and_auto_gate(monkeypatch) -> None:
    c = _core_stub()
    calls: dict[str, Any] = {}

    def _build(_self: Any, mode: str, only_risk: str, domain: str, check_id: str) -> dict[str, Any]:
        calls["mode"] = mode
        calls["only_risk"] = only_risk
        calls["domain"] = domain
        calls["check_id"] = check_id
        return {"status": "ok", "plan": []}

    monkeypatch.setattr(core_mod, "build_self_repair_plan", _build)
    monkeypatch.setattr(core_mod, "run_self_repair_plan", lambda _self, plan, mode="apply": {**plan, "status": "applied"})

    out1 = c.run_self_repair(mode="invalid")
    assert out1["status"] == "ok"
    assert calls["mode"] == "dry-run"

    out2 = c.run_self_repair(mode="apply", auto=True, risk="R3", approve_r3=True)
    assert out2["status"] == "applied"
    assert calls["mode"] == "apply"
    # auto + config disallow -> downgraded risk for planner call
    assert calls["only_risk"] == "R1"
    assert out2["r3_approved"] is True


def test_get_history_and_rollback_error_wrappers(monkeypatch) -> None:
    c = _core_stub()
    monkeypatch.setattr(core_mod, "list_repair_history", lambda _m, limit=10: (_ for _ in ()).throw(RuntimeError("h")))
    monkeypatch.setattr(core_mod, "rollback_self_repair_operation", lambda _s, _op: (_ for _ in ()).throw(RuntimeError("r")))
    hist = c.get_self_repair_history(limit=3)
    rb = c.rollback_self_repair_operation("op-1")
    assert hist["status"] == "error"
    assert rb["status"] == "error"
    assert rb["operation_id"] == "op-1"


def test_run_maintenance_policy_disabled_and_cooldown(monkeypatch) -> None:
    c = _core_stub()
    c.config["settings"]["memory"]["maintenance_policy"]["enabled"] = False
    assert c._run_maintenance_policy()["status"] == "disabled"

    c.config["settings"]["memory"]["maintenance_policy"]["enabled"] = True
    monkeypatch.setattr(core_mod, "gather_policy_stats", lambda _ws, _cfg: {"x": 1})
    monkeypatch.setattr(
        core_mod,
        "build_policy_actions",
        lambda _stats: [SimpleNamespace(action="cleanup_test_memories", reason="due")],
    )
    monkeypatch.setattr(c, "_load_maintenance_policy_state", lambda: {"last_runs": {"cleanup_test_memories": "2026-05-20T00:00:00+00:00"}})
    monkeypatch.setattr(c, "_policy_action_due", lambda state, action, cooldown_hours: False)
    monkeypatch.setattr(c, "_save_maintenance_policy_state", lambda _s: None)
    monkeypatch.setattr(c, "_append_maintenance_policy_log", lambda _p: None)
    out = c._run_maintenance_policy(force=False)
    assert out["status"] == "success"
    assert out["ran"] == []
    assert out["skipped"][0]["reason"] == "cooldown"
