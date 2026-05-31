from __future__ import annotations

import json
from pathlib import Path

from ms8.engine_core.maintenance.self_check import check_runner, reporter


def test_runner_time_and_process_helpers(monkeypatch) -> None:
    assert check_runner._to_aware("bad-ts") is None
    assert check_runner._to_aware("2026-05-01T00:00:00Z") is not None

    class _R:
        returncode = 1
        stdout = ""

    monkeypatch.setattr(check_runner.subprocess, "run", lambda *a, **k: _R())
    assert check_runner._process_start_text(123) == ""

    monkeypatch.setattr(check_runner.os, "kill", lambda *_a, **_k: None)
    assert check_runner._pid_alive(999) is True


def test_runner_write_markdown_and_emit_health_ok(monkeypatch, tmp_path: Path) -> None:
    md = tmp_path / "r.md"
    report = {
        "started_at": "a",
        "finished_at": "b",
        "requested_level": "L4",
        "summary": {"total": 1, "pass": 0, "warn": 1, "fail": 0, "error": 0, "exit_code": 1},
        "results": [{"check_id": "c1", "level": "L4", "status": "warn", "message": "m", "action_guide": "do"}],
    }
    check_runner._write_report_markdown(md, report)
    text = md.read_text(encoding="utf-8")
    assert "Self Check Report" in text and "c1" in text

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
            return False

    monkeypatch.setattr(check_runner.urllib.request, "urlopen", lambda *_a, **_k: _Resp())
    cfg = {"settings": {"memory": {"self_check": {"healthchecks_enabled": True, "healthchecks_url": "https://x"}}}}
    out = check_runner._emit_healthchecks_ping(cfg, {"summary": {"exit_code": 0}})
    assert out["status"] == "ok"


def test_runner_run_one_timeout_override(monkeypatch) -> None:
    class _Spec:
        check_id = "t1"
        level = "L1"
        domain = "memory"
        timeout_s = 0.2
        action_guide = "x"

        @staticmethod
        def fn(_core, _ctx):
            return {"status": "pass", "message": "ok", "details": {}}

    class _Timer:
        def __init__(self, *_a, **_k):
            self._fn = _a[1]

        def start(self):
            self._fn()  # mark timeout immediately

        def cancel(self):
            return None

    monkeypatch.setattr(check_runner.threading, "Timer", _Timer)
    out = check_runner._run_one(_Spec(), object(), {})
    assert out["status"] == "error"
    assert out["message"] == "timeout"


def test_reporter_render_markdown_and_maturity_gate(tmp_path: Path) -> None:
    rep = {
        "started_at": "a",
        "finished_at": "b",
        "requested_level": "L4",
        "status": "warn",
        "summary": {"total": 2, "pass": 1, "warn": 1, "fail": 0, "error": 0, "exit_code": 1},
        "results": [
            {"check_id": "m1", "status": "pass", "level": "L4", "domain": "memory", "message": "ok"},
            {"check_id": "s1", "status": "warn", "level": "L4", "domain": "security", "message": "warn"},
        ],
    }
    rep["domain_summary"] = reporter._domain_summary(rep)
    rep["maturity_gate"] = reporter._maturity_gate(rep)
    md = reporter.render_markdown(rep)
    assert "Domain Coverage" in md
    assert "Maturity Gate" in md

    reports = tmp_path / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "self_check_latest.json").write_text(json.dumps(rep), encoding="utf-8")
    loaded = reporter.load_latest(tmp_path)
    assert loaded["status"] in {"warn", "ok", "pass"}
