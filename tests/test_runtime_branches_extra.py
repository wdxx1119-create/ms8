from __future__ import annotations

import json
from pathlib import Path

from ms8 import runtime


def test_maintenance_window_status_invalid_json(monkeypatch, tmp_path: Path) -> None:
    p = tmp_path / "maintenance_window.json"
    p.write_text("{bad", encoding="utf-8")
    monkeypatch.setattr(
        runtime,
        "ensure_runtime_dirs",
        lambda: {"maintenance_window": p},
    )
    out = runtime.maintenance_window_status()
    assert out["enabled"] is False


def test_set_maintenance_window_disable_unlinks(monkeypatch, tmp_path: Path) -> None:
    p = tmp_path / "maintenance_window.json"
    p.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(runtime, "ensure_runtime_dirs", lambda: {"maintenance_window": p})
    out = runtime.set_maintenance_window(False)
    assert out["enabled"] is False
    assert not p.exists()


def test_write_memory_skips_for_auto_source_in_window(monkeypatch) -> None:
    monkeypatch.setattr(
        runtime,
        "maintenance_window_status",
        lambda: {"enabled": True, "pause_maintenance_writes": True},
    )
    out = runtime.write_memory("x", source="maintenance")
    assert out["status"] == "skipped"
    assert out["reason"] == "maintenance_window"


def test_write_memory_handles_quarantine_validation_error(monkeypatch, tmp_path: Path) -> None:
    class _Engine:
        @staticmethod
        def write_memory(text: str, source: str = "demo"):
            return {"id": "1", "text": text, "source": source}

    monkeypatch.setattr(runtime, "maintenance_window_status", lambda: {"enabled": False})
    monkeypatch.setattr(runtime, "_engine", lambda: _Engine())
    monkeypatch.setattr(
        runtime,
        "ensure_runtime_dirs",
        lambda: {"memories": tmp_path / "m.jsonl", "quarantine": tmp_path / "q.jsonl", "activity": tmp_path / "a.json"},
    )
    monkeypatch.setattr(runtime, "validate_file_and_quarantine", lambda *_a, **_k: (_ for _ in ()).throw(OSError("x")))
    monkeypatch.setattr(runtime, "_touch_activity", lambda _evt: None)
    out = runtime.write_memory("hello", source="user")
    assert out["id"] == "1"


def test_run_maintenance_policy_method_missing(monkeypatch) -> None:
    class _Engine:
        _core = object()

    monkeypatch.setattr(runtime, "maintenance_window_status", lambda: {"enabled": False})
    monkeypatch.setattr(runtime, "_engine", lambda: _Engine())
    out = runtime.run_maintenance_policy()
    assert out["ok"] is False
    assert out["reason"] == "method_missing"


def test_run_maintenance_policy_exception(monkeypatch) -> None:
    class _Core:
        @staticmethod
        def maintenance_policy():
            raise RuntimeError("boom")

    class _Engine:
        _core = _Core()

    monkeypatch.setattr(runtime, "maintenance_window_status", lambda: {"enabled": False})
    monkeypatch.setattr(runtime, "_engine", lambda: _Engine())
    out = runtime.run_maintenance_policy()
    assert out["ok"] is False
    assert out["error_code"] == "E_RUNTIME_MAINTENANCE_POLICY_FAILED"


def test_run_weekly_compression_updates_state(monkeypatch, tmp_path: Path) -> None:
    memory = tmp_path / "memory"
    memory.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(runtime, "ensure_runtime_dirs", lambda: {"memory": memory, "compression_state": memory / "compression_state.json"})
    monkeypatch.setattr(
        runtime,
        "_run_core_method",
        lambda *a, **k: {"ok": True, "result": {"status": "success", "compressed": 2}},
    )
    out = runtime.run_weekly_compression(confirm=True)
    assert out["ok"] is True
    state = json.loads((memory / "compression_state.json").read_text(encoding="utf-8"))
    assert state["status"] == "success"
    assert state["consecutive_failures"] == 0
