from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ms8.engine_core.maintenance.self_repair.repair_audit import (
    append_repair_audit,
    list_repair_history,
    load_latest_repair_report,
    save_repair_report,
    summarize_repair_7d,
)
from ms8.engine_core.maintenance.self_repair.repair_schema import RepairExecutionRow
from ms8.engine_core.maintenance.self_repair.repair_validator import (
    run_check_once,
    verify_repair,
)


def _mk_row(result: str, *, rolled_back: bool = False, timestamp: str = "2099-01-01T00:00:00+00:00") -> RepairExecutionRow:
    return RepairExecutionRow(
        operation_id="op-1",
        check_id="c-1",
        action="do_x",
        domain="memory",
        risk="R1",
        mode="dry-run",
        result=result,
        verify_status="pass",
        rolled_back=rolled_back,
        timestamp=timestamp,
    )


def test_repair_audit_append_save_load_and_history(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)

    append_repair_audit(memory_dir, _mk_row("success"))
    append_repair_audit(memory_dir, _mk_row("failed_verify", rolled_back=True))

    report = {
        "mode": "dry-run",
        "status": "ok",
        "started_at": "2099-01-01T00:00:00+00:00",
        "summary": {"success": 1, "failed": 1, "rolled_back": 1, "needs_manual": 1},
    }
    paths = save_repair_report(memory_dir, report)
    assert Path(paths["latest"]).exists()
    assert Path(paths["history"]).exists()

    latest = load_latest_repair_report(memory_dir)
    assert latest["mode"] == "dry-run"
    assert latest["summary"]["success"] == 1

    history = list_repair_history(memory_dir, limit=5)
    assert len(history) >= 1
    assert history[0]["success"] == 1
    assert history[0]["failed"] == 1
    assert history[0]["rolled_back"] == 1


def test_load_latest_repair_report_missing_and_invalid(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    out_missing = load_latest_repair_report(memory_dir)
    assert out_missing["status"] == "missing"

    latest = memory_dir / "reports" / "repair_latest.json"
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text("{invalid", encoding="utf-8")
    out_invalid = load_latest_repair_report(memory_dir)
    assert out_invalid["status"] == "error"


def test_list_repair_history_skips_bad_json_and_respects_limit(tmp_path: Path) -> None:
    hist_dir = tmp_path / "memory" / "reports" / "repair_history"
    hist_dir.mkdir(parents=True, exist_ok=True)
    (hist_dir / "repair-a.json").write_text("{}", encoding="utf-8")
    (hist_dir / "repair-b.json").write_text("{bad", encoding="utf-8")
    (hist_dir / "repair-c.json").write_text(
        json.dumps({"summary": {"success": 3, "failed": 0, "rolled_back": 0, "needs_manual": 0}}),
        encoding="utf-8",
    )

    out = list_repair_history(tmp_path / "memory", limit=1)
    assert len(out) == 1


def test_summarize_repair_7d_empty_and_mixed(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    empty = summarize_repair_7d(memory_dir, days=7)
    assert empty["total"] == 0
    assert empty["success_rate"] == 0.0

    append_repair_audit(memory_dir, _mk_row("success", timestamp="2099-01-01T00:00:00+00:00"))
    append_repair_audit(memory_dir, _mk_row("error", timestamp="2099-01-01T01:00:00+00:00"))
    append_repair_audit(memory_dir, _mk_row("blocked", timestamp="2099-01-01T02:00:00+00:00", rolled_back=True))

    # Add malformed line; summarizer should ignore it.
    audit = memory_dir / "logs" / "repair_ops_audit.jsonl"
    with audit.open("a", encoding="utf-8") as f:
        f.write("{bad-json}\n")

    summary = summarize_repair_7d(memory_dir, days=36500)
    assert summary["total"] == 3
    assert summary["success"] == 1
    assert summary["failed"] == 2
    assert summary["rolled_back"] == 1
    assert summary["needs_manual"] == 1


@dataclass
class _Spec:
    check_id: str
    fn: object
    level: str = "L4"
    domain: str = "memory"


class _Core:
    pass


def test_run_check_once_not_found(monkeypatch) -> None:
    monkeypatch.setattr(
        "ms8.engine_core.maintenance.self_repair.repair_validator.build_check_specs",
        lambda level="FULL_PLUS": [],
    )
    out = run_check_once(_Core(), "x")
    assert out["status"] == "skipped"


def test_run_check_once_invalid_and_exception(monkeypatch) -> None:
    spec_invalid = _Spec(check_id="x", fn=lambda _core, _ctx: 1)

    def _boom(_core, _ctx):
        raise RuntimeError("boom")

    spec_boom = _Spec(check_id="y", fn=_boom)
    monkeypatch.setattr(
        "ms8.engine_core.maintenance.self_repair.repair_validator.build_check_specs",
        lambda level="FULL_PLUS": [spec_invalid, spec_boom],
    )

    out_invalid = run_check_once(_Core(), "x")
    assert out_invalid["status"] == "error"
    assert out_invalid["message"] == "invalid_check_result"

    out_boom = run_check_once(_Core(), "y")
    assert out_boom["status"] == "error"
    assert "exception:boom" in out_boom["message"]


def test_verify_repair_status_variants(monkeypatch) -> None:
    spec_pass = _Spec(check_id="p", fn=lambda _core, _ctx: {"status": "pass"})
    spec_warn = _Spec(check_id="w", fn=lambda _core, _ctx: {"status": "warn"})
    spec_fail = _Spec(check_id="f", fn=lambda _core, _ctx: {"status": "fail"})
    monkeypatch.setattr(
        "ms8.engine_core.maintenance.self_repair.repair_validator.build_check_specs",
        lambda level="FULL_PLUS": [spec_pass, spec_warn, spec_fail],
    )

    assert verify_repair(_Core(), "p")["ok"] is True
    warn = verify_repair(_Core(), "w")
    assert warn["ok"] is True
    assert warn["partial"] is True
    fail = verify_repair(_Core(), "f")
    assert fail["ok"] is False
