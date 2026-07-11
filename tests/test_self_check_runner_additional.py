from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from ms8.engine_core.maintenance.self_check import check_runner


def test_summarize_exit_codes() -> None:
    ok = check_runner._summarize([{"status": "pass"}, {"status": "warn"}])
    assert ok["exit_code"] == 1
    bad = check_runner._summarize([{"status": "error"}])
    assert bad["exit_code"] == 2


def test_run_one_invalid_output_marks_error() -> None:
    class _Spec:
        check_id = "x"
        level = "L1"
        domain = "memory"
        timeout_s = 0.1
        action_guide = "fix"

        @staticmethod
        def fn(_core, _ctx):
            return "invalid"

    row = check_runner._run_one(_Spec(), object(), {})
    assert row["status"] == "error"
    assert "invalid check output" in row["message"]


def test_run_self_check_concurrent_skip(monkeypatch, tmp_path: Path) -> None:
    reports = tmp_path / "memory" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "check_in_progress.json").write_text(
        json.dumps({"pid": 999999, "process_start": "x", "started_at": "2026-05-01T00:00:00+00:00"}, ensure_ascii=False),
        encoding="utf-8",
    )

    # force lock contention branch without depending on a platform lock module
    class _Lock:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def fileno(self):
            return 1

        def close(self):
            return None

    original_open = check_runner.Path.open

    def _fake_open(self: Path, *args, **kwargs):
        if self.name == "check.lock":
            return _Lock()
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(check_runner.Path, "open", _fake_open)  # type: ignore[arg-type]

    def _raise_blocking(*_a, **_k):
        raise BlockingIOError()

    monkeypatch.setattr(check_runner, "_lock_file_nonblocking", _raise_blocking)
    monkeypatch.setattr(check_runner, "_unlock_file", lambda *_a, **_k: None)

    class _Core:
        config = {
            "memory_dir": str(tmp_path / "memory"),
            "workspace_dir": str(tmp_path),
            "settings": {"memory": {"self_check": {}}},
        }

    out = check_runner.run_self_check(_Core(), level="L1")
    assert out["status"] == "skipped"
    assert out["reason"] == "check_skipped_concurrent"


def test_load_latest_report_proxy(monkeypatch, tmp_path: Path) -> None:
    called = {}

    def _fake(path: Path):
        called["path"] = path
        return {"ok": True}

    monkeypatch.setattr(check_runner, "reporter_load_latest", _fake)
    out = check_runner.load_latest_report({"memory_dir": str(tmp_path / "memory")})
    assert out["ok"] is True
    assert called["path"] == tmp_path / "memory"


def test_run_self_check_heartbeat_and_shadow_audit_error_paths(monkeypatch, tmp_path: Path) -> None:
    # one minimal passing spec to keep run_self_check deterministic
    spec = SimpleNamespace(
        check_id="x",
        level="L1",
        domain="memory",
        timeout_s=0.2,
        action_guide="none",
        fn=lambda _core, _ctx: {"status": "pass", "message": "ok", "details": {}},
    )
    monkeypatch.setattr(check_runner, "build_check_specs", lambda level="L1": [spec])
    monkeypatch.setattr(check_runner, "_emit_healthchecks_ping", lambda *_a, **_k: {"status": "disabled"})
    monkeypatch.setattr(check_runner, "persist_report", lambda *_a, **_k: {"ok": True})

    # force heartbeat write failure branch
    orig_write_text = Path.write_text

    def _fail_heartbeat(self: Path, *args, **kwargs):
        if self.name == "heartbeat":
            raise OSError("heartbeat denied")
        return orig_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", _fail_heartbeat)

    class _Shadow:
        def record_data(self, **kwargs):
            raise RuntimeError("shadow failed")

    class _Core:
        shadow = _Shadow()
        config = {
            "memory_dir": str(tmp_path / "memory"),
            "workspace_dir": str(tmp_path),
            "settings": {"memory": {"self_check": {"heartbeat_path": str(tmp_path / "heartbeat")}}},
        }

    out = check_runner.run_self_check(_Core(), level="L1")
    assert out["summary"]["exit_code"] == 0
    assert out["healthchecks"]["status"] == "disabled"
