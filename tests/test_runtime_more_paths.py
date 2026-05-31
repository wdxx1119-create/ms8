from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ms8 import runtime


def test_set_and_read_maintenance_window_enabled(monkeypatch, tmp_path: Path) -> None:
    p = tmp_path / "maintenance_window.json"
    monkeypatch.setattr(runtime, "ensure_runtime_dirs", lambda: {"maintenance_window": p})
    out = runtime.set_maintenance_window(
        True,
        reason="test",
        pause_session_ingestion=False,
        pause_maintenance_writes=True,
        pause_review_writes=False,
        pause_compression_writes=True,
    )
    assert out["enabled"] is True
    status = runtime.maintenance_window_status()
    assert status["enabled"] is True
    assert status["reason"] == "test"
    assert status["pause_session_ingestion"] is False
    assert status["pause_maintenance_writes"] is True
    assert status["pause_review_writes"] is False
    assert status["pause_compression_writes"] is True


def test_maintenance_window_status_non_dict_json(monkeypatch, tmp_path: Path) -> None:
    p = tmp_path / "maintenance_window.json"
    p.write_text('["not-dict"]', encoding="utf-8")
    monkeypatch.setattr(runtime, "ensure_runtime_dirs", lambda: {"maintenance_window": p})
    out = runtime.maintenance_window_status()
    assert out["enabled"] is False


def test_has_recent_activity_true_and_false(monkeypatch, tmp_path: Path) -> None:
    activity = tmp_path / "activity.json"
    monkeypatch.setattr(runtime, "ensure_runtime_dirs", lambda: {"activity": activity})
    now = datetime.now(timezone.utc)
    activity.write_text(json.dumps({"event": "x", "at": now.isoformat()}), encoding="utf-8")
    assert runtime.has_recent_activity(300) is True
    old = now - timedelta(seconds=1000)
    activity.write_text(json.dumps({"event": "x", "at": old.isoformat()}), encoding="utf-8")
    assert runtime.has_recent_activity(300) is False


def test_has_recent_activity_invalid_payload(monkeypatch, tmp_path: Path) -> None:
    activity = tmp_path / "activity.json"
    activity.write_text("{bad", encoding="utf-8")
    monkeypatch.setattr(runtime, "ensure_runtime_dirs", lambda: {"activity": activity})
    assert runtime.has_recent_activity(300) is False


def test_write_memory_does_not_skip_user_source(monkeypatch) -> None:
    class _Engine:
        @staticmethod
        def write_memory(text: str, source: str = "demo"):
            return {"id": "ok", "text": text, "source": source}

    monkeypatch.setattr(runtime, "maintenance_window_status", lambda: {"enabled": True})
    monkeypatch.setattr(runtime, "_engine", lambda: _Engine())
    monkeypatch.setattr(runtime, "ensure_runtime_dirs", lambda: {"memories": Path("/tmp/m"), "quarantine": Path("/tmp/q"), "activity": Path("/tmp/a")})
    monkeypatch.setattr(runtime, "validate_file_and_quarantine", lambda *_a, **_k: None)
    monkeypatch.setattr(runtime, "_touch_activity", lambda _evt: None)
    out = runtime.write_memory("hello", source="user")
    assert out["id"] == "ok"


def test_write_compression_state_success_and_failure(monkeypatch, tmp_path: Path) -> None:
    state = tmp_path / "compression_state.json"
    monkeypatch.setattr(runtime, "ensure_runtime_dirs", lambda: {"compression_state": state})
    runtime._write_compression_state(status="success", ran=True, reason="ok", result={"a": 1})
    payload = json.loads(state.read_text(encoding="utf-8"))
    assert payload["consecutive_failures"] == 0
    assert payload["status"] == "success"
    assert payload["last_result"]["a"] == 1

    runtime._write_compression_state(status="failed", ran=True, reason="boom", result={"b": 2})
    payload2 = json.loads(state.read_text(encoding="utf-8"))
    assert payload2["consecutive_failures"] == 1
    assert payload2["status"] == "failed"

