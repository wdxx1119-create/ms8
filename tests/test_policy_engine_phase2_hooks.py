from __future__ import annotations

import json
from pathlib import Path

from ms8.engine_core.maintenance.self_check import check_runner
from ms8.engine_core.maintenance.self_repair import repair_orchestrator


def test_self_check_uses_policy_engine_check_id_override(monkeypatch, tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    (memory_dir / "reports").mkdir(parents=True, exist_ok=True)

    class _SpecA:
        check_id = "a"
        level = "L1"
        domain = "memory"
        timeout_s = 0.1
        action_guide = "ga"

        @staticmethod
        def fn(_core, _ctx):
            return {"status": "pass", "message": "A", "details": {}}

    class _SpecB:
        check_id = "b"
        level = "L1"
        domain = "memory"
        timeout_s = 0.1
        action_guide = "gb"

        @staticmethod
        def fn(_core, _ctx):
            return {"status": "pass", "message": "B", "details": {}}

    class _Engine:
        def run_self_check_specs(self, payload):
            assert payload["check_ids"] == ["a", "b"]
            return {
                "ok": True,
                "code": "OK",
                "reason": "t",
                "trace_id": "t1",
                "data": {"check_ids": ["b"]},
            }

    class _Core:
        shadow = None
        config = {
            "memory_dir": str(memory_dir),
            "workspace_dir": str(tmp_path),
            "settings": {"memory": {"self_check": {}}},
        }

    monkeypatch.setattr(check_runner, "build_check_specs", lambda level="L1": [_SpecA(), _SpecB()])
    monkeypatch.setattr(check_runner, "get_policy_engine", lambda: _Engine())
    monkeypatch.setattr(
        check_runner,
        "persist_report",
        lambda *_a, **_k: {"latest_json": "x", "history_file": "y"},
    )
    monkeypatch.setattr(check_runner, "_emit_healthchecks_ping", lambda *_a, **_k: {"status": "disabled"})

    out = check_runner.run_self_check(_Core(), level="L1")
    ids = [row["check_id"] for row in out["results"]]
    assert ids == ["b"]


def test_repair_plan_uses_policy_engine_override(monkeypatch, tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    reports = memory_dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "self_check_latest.json").write_text(
        json.dumps(
            {
                "status": "ok",
                "requested_level": "L1",
                "results": [
                    {"check_id": "l1_disk_space", "status": "warn", "message": "disk", "details": {}}
                ],
            }
        ),
        encoding="utf-8",
    )

    class _Policy:
        action = "cleanup_disk"
        domain = "memory"
        risk = "R1"
        target = "memory:logs_backups"
        depends_on = []

    class _Engine:
        def plan_self_repair(self, payload):
            assert payload["plan"]
            return {
                "ok": True,
                "code": "OK",
                "reason": "t",
                "trace_id": "t2",
                "data": {
                    "plan": [
                        {
                            "operation_id": "override-op",
                            "check_id": "l1_disk_space",
                            "action": "cleanup_disk",
                            "domain": "memory",
                            "risk": "R1",
                            "reason": "override",
                            "target": "memory:logs_backups",
                            "depends_on": [],
                            "params": {},
                            "action_guide": "",
                        }
                    ]
                },
            }

    class _Core:
        config = {"memory_dir": str(memory_dir)}

    monkeypatch.setattr(repair_orchestrator, "get_policy", lambda _cid: _Policy())
    monkeypatch.setattr(repair_orchestrator, "get_policy_engine", lambda: _Engine())

    out = repair_orchestrator.build_repair_plan(_Core(), mode="dry-run")
    assert out["plan_count"] == 1
    assert out["plan"][0]["operation_id"] == "override-op"

