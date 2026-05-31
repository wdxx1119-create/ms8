from __future__ import annotations

import json
from pathlib import Path

from ms8.engine_core.maintenance.self_check import check_runner


def test_emit_healthchecks_ping_disabled_and_missing_url() -> None:
    cfg_disabled = {"settings": {"memory": {"self_check": {"healthchecks_enabled": False}}}}
    out1 = check_runner._emit_healthchecks_ping(cfg_disabled, {"summary": {"exit_code": 0}})
    assert out1["status"] == "disabled"

    cfg_missing = {"settings": {"memory": {"self_check": {"healthchecks_enabled": True, "healthchecks_url": ""}}}}
    out2 = check_runner._emit_healthchecks_ping(cfg_missing, {"summary": {"exit_code": 0}})
    assert out2["status"] == "skipped"
    assert out2["reason"] == "missing_url"


def test_emit_healthchecks_ping_error_branch(monkeypatch) -> None:
    cfg = {
        "settings": {
            "memory": {
                "self_check": {
                    "healthchecks_enabled": True,
                    "healthchecks_url": "https://example.local/ping",
                    "healthchecks_fail_suffix": "/fail",
                }
            }
        }
    }

    def _raise_url_error(*_a, **_k):
        raise check_runner.urllib.error.URLError("boom")

    monkeypatch.setattr(check_runner.urllib.request, "urlopen", _raise_url_error)
    out = check_runner._emit_healthchecks_ping(cfg, {"summary": {"exit_code": 2}})
    assert out["status"] == "error"
    assert out["url"].endswith("/fail")


def test_run_self_check_success_path_and_stale_progress_cleanup(monkeypatch, tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    reports = memory_dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    progress = reports / "check_in_progress.json"
    progress.write_text(
        json.dumps({"pid": 999999, "process_start": "stale", "started_at": "2026-05-01T00:00:00+00:00"}),
        encoding="utf-8",
    )

    class _Spec:
        check_id = "c1"
        level = "L1"
        domain = "memory"
        timeout_s = 0.1
        action_guide = "guide"

        @staticmethod
        def fn(_core, _ctx):
            return {"status": "pass", "message": "ok", "details": {"k": 1}}

    monkeypatch.setattr(check_runner, "build_check_specs", lambda level="L1": [_Spec()])
    monkeypatch.setattr(check_runner, "_pid_alive", lambda _pid: False)
    monkeypatch.setattr(check_runner, "_process_start_text", lambda _pid: "")
    monkeypatch.setattr(
        check_runner,
        "persist_report",
        lambda *_a, **_k: {"latest_json": "x", "history_file": "y"},
    )
    monkeypatch.setattr(check_runner, "_emit_healthchecks_ping", lambda *_a, **_k: {"status": "disabled"})

    class _Core:
        shadow = None
        config = {
            "memory_dir": str(memory_dir),
            "workspace_dir": str(tmp_path),
            "settings": {"memory": {"self_check": {}}},
        }

    out = check_runner.run_self_check(_Core(), level="l1")
    assert out["requested_level"] == "L1"
    assert out["summary"]["exit_code"] == 0
    assert out["interrupted_last_run"] is True
    assert out["persist"]["latest_json"] == "x"

