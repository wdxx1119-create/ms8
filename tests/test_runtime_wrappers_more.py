from __future__ import annotations

import json
from pathlib import Path

from ms8 import runtime


def test_run_maintenance_now_paths(monkeypatch) -> None:
    class _EngineOk:
        @staticmethod
        def run_maintenance_now(force: bool = True):  # noqa: FBT001
            return {"force": force, "ok": True}

    monkeypatch.setattr(runtime, "maintenance_window_status", lambda: {"enabled": False})
    monkeypatch.setattr(runtime, "_engine", lambda: _EngineOk())
    out_ok = runtime.run_maintenance_now(force=False)
    assert out_ok["ok"] is True
    assert out_ok["method"] == "engine.run_maintenance_now"

    class _EngineErr:
        @staticmethod
        def run_maintenance_now(force: bool = True):  # noqa: FBT001
            raise RuntimeError("boom")

    monkeypatch.setattr(runtime, "_engine", lambda: _EngineErr())
    out_err = runtime.run_maintenance_now(force=True)
    assert out_err["ok"] is False
    assert out_err["error_code"] == "E_RUNTIME_MAINTENANCE_NOW_FAILED"

    monkeypatch.setattr(runtime, "_engine", lambda: object())
    monkeypatch.setattr(runtime, "run_maintenance_policy", lambda: {"ok": True, "via": "fallback"})
    out_fb = runtime.run_maintenance_now(force=True)
    assert out_fb["via"] == "fallback"


def test_run_core_method_branches(monkeypatch) -> None:
    class _EngineNoCore:
        _core = None

    monkeypatch.setattr(runtime, "_engine", lambda: _EngineNoCore())
    out_no = runtime._run_core_method("x")
    assert out_no["reason"] == "core_unavailable"

    class _CoreMissing:
        pass

    class _EngineMissing:
        _core = _CoreMissing()

    monkeypatch.setattr(runtime, "_engine", lambda: _EngineMissing())
    out_missing = runtime._run_core_method("x")
    assert out_missing["reason"] == "method_missing"

    class _CoreErr:
        @staticmethod
        def x():
            raise ValueError("bad")

    class _EngineErr:
        _core = _CoreErr()

    monkeypatch.setattr(runtime, "_engine", lambda: _EngineErr())
    out_err = runtime._run_core_method("x")
    assert out_err["ok"] is False
    assert out_err["error_code"] == "E_RUNTIME_CORE_METHOD_FAILED"


def test_run_weekly_compression_failure_state(monkeypatch, tmp_path: Path) -> None:
    memory = tmp_path / "memory"
    memory.mkdir(parents=True, exist_ok=True)
    state_file = memory / "compression_state.json"
    state_file.write_text(json.dumps({"consecutive_failures": 2}), encoding="utf-8")

    monkeypatch.setattr(runtime, "ensure_runtime_dirs", lambda: {"memory": memory, "compression_state": state_file})
    monkeypatch.setattr(runtime, "_run_core_method", lambda *a, **k: {"ok": False, "error": "x"})
    out = runtime.run_weekly_compression(confirm=False)
    assert out["ok"] is False
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["status"] == "error"
    assert state["consecutive_failures"] == 2


def test_run_daily_learning_unavailable_and_error(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "maintenance_window_status", lambda: {"enabled": False})

    class _EngineNoCore:
        _core = None

    monkeypatch.setattr(runtime, "_engine", lambda: _EngineNoCore())
    out_no = runtime.run_daily_learning()
    assert out_no["reason"] == "trigger_daily_learning_unavailable"

    class _Core:
        @staticmethod
        def trigger_daily_learning(date_str=None):  # noqa: ANN001
            raise OSError("oops")

    class _Engine:
        _core = _Core()

    monkeypatch.setattr(runtime, "_engine", lambda: _Engine())
    out_err = runtime.run_daily_learning("2026-05-19")
    assert out_err["ok"] is False
    assert out_err["error_code"] == "E_RUNTIME_DAILY_LEARNING_FAILED"
