from __future__ import annotations

import asyncio
import builtins
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
        "settings": {"memory": {"self_check": {"allow_r3_auto_apply": False}}},
    }
    c.crypto = SimpleNamespace(is_enabled=lambda: False, is_unlocked=lambda: True)
    return c


def test_get_self_check_report_and_self_repair_report_error_wrappers(monkeypatch) -> None:
    c = _core_stub()

    monkeypatch.setattr(core_mod, "load_latest_report", lambda _cfg: (_ for _ in ()).throw(RuntimeError("sc")))
    monkeypatch.setattr(
        core_mod, "load_latest_repair_report", lambda _mem_dir: (_ for _ in ()).throw(RuntimeError("sr"))
    )

    out_sc = c.get_self_check_report()
    out_sr = c.get_self_repair_report()
    assert out_sc["status"] == "error"
    assert "sc" in out_sc["error"]
    assert out_sr["status"] == "error"
    assert "sr" in out_sr["error"]


def test_run_self_repair_exception_wrapper(monkeypatch) -> None:
    c = _core_stub()
    monkeypatch.setattr(
        core_mod, "build_self_repair_plan", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("plan boom"))
    )
    out = c.run_self_repair(mode="apply", risk="R1")
    assert out["status"] == "error"
    assert out["mode"] == "apply"


def test_evaluate_admission_fallback_when_import_fails(monkeypatch) -> None:
    c = _core_stub()

    real_import = builtins.__import__

    def _bad_import(name, *args, **kwargs):  # noqa: ANN001
        if name == ".admission_compat":
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _bad_import)
    out = c._evaluate_admission("  hello  ", source="x")
    assert out["normalized_text"] == "hello"
    assert out["route"] == "accepted"
    assert out["should_persist_main"] is True


def test_run_async_running_loop_path(monkeypatch) -> None:
    c = _core_stub()

    async def _coro() -> int:
        return 11

    monkeypatch.setattr(asyncio, "get_running_loop", lambda: object())
    out = c._run_async(_coro())
    assert out == 11


def test_run_async_thread_runtime_error_raises(monkeypatch) -> None:
    c = _core_stub()

    async def _coro() -> int:
        return 12

    monkeypatch.setattr(asyncio, "get_running_loop", lambda: object())

    def _boom_run(_value: Any) -> Any:
        raise RuntimeError("loop-broken")

    monkeypatch.setattr(asyncio, "run", _boom_run)
    coro = _coro()
    try:
        c._run_async(coro)
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "loop-broken" in str(exc)
    finally:
        try:
            coro.close()
        except RuntimeError:
            pass
