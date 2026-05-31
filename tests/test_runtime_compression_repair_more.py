from __future__ import annotations

from typing import Any

from ms8 import runtime


def test_repair_compression_if_stale_core_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "_engine", lambda: object())
    monkeypatch.setattr(runtime, "_write_compression_state", lambda **_k: None)
    out = runtime.repair_compression_if_stale()
    assert out["ok"] is False
    assert out["reason"] == "core_unavailable"


def test_repair_compression_if_stale_not_stale(monkeypatch) -> None:
    class _Core:
        def get_monitoring_status(self) -> dict[str, Any]:
            return {"compression_freshness": {"hours_since_last": 1}, "alerts": []}

    class _Engine:
        _core = _Core()

    monkeypatch.setattr(runtime, "_engine", lambda: _Engine())
    monkeypatch.setattr(runtime, "_write_compression_state", lambda **_k: None)
    out = runtime.repair_compression_if_stale(stale_hours_threshold=48)
    assert out["ok"] is True
    assert out["ran"] is False
    assert out["reason"] == "not_stale"


def test_repair_compression_if_stale_trigger_success(monkeypatch) -> None:
    class _Core:
        def get_monitoring_status(self) -> dict[str, Any]:
            return {"compression_freshness": {"hours_since_last": 96}, "alerts": []}

        def trigger_weekly_compression(self, confirm: bool = False) -> dict[str, Any]:
            assert confirm is True
            return {"status": "ok"}

    class _Engine:
        _core = _Core()

    monkeypatch.setattr(runtime, "_engine", lambda: _Engine())
    monkeypatch.setattr(runtime, "_write_compression_state", lambda **_k: None)
    out = runtime.repair_compression_if_stale(stale_hours_threshold=48)
    assert out["ok"] is True
    assert out["ran"] is True
    assert out["method"] == "trigger_weekly_compression"


def test_repair_compression_if_stale_trigger_fails_and_fallback_maintenance(monkeypatch) -> None:
    class _Core:
        def get_monitoring_status(self) -> dict[str, Any]:
            return {"compression_freshness": {"hours_since_last": 96}, "alerts": []}

        def trigger_weekly_compression(self, confirm: bool = False) -> dict[str, Any]:
            raise RuntimeError("boom")

    class _Engine:
        _core = _Core()

    monkeypatch.setattr(runtime, "_engine", lambda: _Engine())
    monkeypatch.setattr(runtime, "_write_compression_state", lambda **_k: None)
    out = runtime.repair_compression_if_stale(stale_hours_threshold=48)
    assert out["ok"] is False
    assert out["method"] == "trigger_weekly_compression"
    assert out["error_code"] == "E_RUNTIME_TRIGGER_WEEKLY_COMPRESSION_FAILED"


def test_repair_compression_if_stale_maintenance_path(monkeypatch) -> None:
    class _Core:
        def get_monitoring_status(self) -> dict[str, Any]:
            return {"alerts": [{"kind": "compression_stale"}]}

        def run_maintenance_now(self, force: bool = False) -> dict[str, Any]:
            assert force is True
            return {"status": "ok"}

    class _Engine:
        _core = _Core()

    monkeypatch.setattr(runtime, "_engine", lambda: _Engine())
    monkeypatch.setattr(runtime, "_write_compression_state", lambda **_k: None)
    out = runtime.repair_compression_if_stale(stale_hours_threshold=48)
    assert out["ok"] is True
    assert out["method"] == "run_maintenance_now"


def test_repair_compression_if_stale_no_method(monkeypatch) -> None:
    class _Core:
        def get_monitoring_status(self) -> dict[str, Any]:
            return {"alerts": [{"kind": "compression_stale"}]}

    class _Engine:
        _core = _Core()

    monkeypatch.setattr(runtime, "_engine", lambda: _Engine())
    monkeypatch.setattr(runtime, "_write_compression_state", lambda **_k: None)
    out = runtime.repair_compression_if_stale(stale_hours_threshold=48)
    assert out["ok"] is False
    assert out["reason"] == "no_repair_method"


def test_repair_duplicates_after_compression_disabled(monkeypatch, tmp_path) -> None:
    root = tmp_path
    (root / "memory").mkdir()
    (root / "health").mkdir()
    (root / "config.json").write_text('{"dedupe":{"enabled": false}}', encoding="utf-8")
    paths = {
        "root": root,
        "memories": root / "memory" / "memories.jsonl",
        "health": root / "health",
        "config_file": root / "config.json",
    }
    monkeypatch.setattr(runtime, "ensure_runtime_dirs", lambda: paths)
    out = runtime.repair_duplicates_after_compression()
    assert out["ok"] is True
    assert out["result"]["reason"] == "dedupe_disabled"


def test_run_engine_self_check_unsupported(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "_engine", lambda: object())
    out = runtime.run_engine_self_check(level="L4")
    assert out["status"] == "unsupported"
    assert out["level"] == "L4"
