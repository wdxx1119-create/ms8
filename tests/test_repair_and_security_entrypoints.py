from __future__ import annotations

import sys

from ms8.engine_core.maintenance.self_repair import repair_cli
from ms8.engine_core.security import cli as security_cli


class _DummyCore:
    def __init__(self):
        self.config = {"memory_dir": "/tmp/ms8-memory"}


def test_security_cli_shim_exports_encryption_main() -> None:
    # compatibility shim should re-export encryption CLI symbols
    assert hasattr(security_cli, "main")


def test_repair_cli_status_history_and_rollback(monkeypatch, capsys) -> None:
    monkeypatch.setattr(repair_cli, "MemoryCore", _DummyCore)
    monkeypatch.setattr(repair_cli, "load_latest_repair_report", lambda _md: {"status": "ok"})
    monkeypatch.setattr(repair_cli, "list_repair_history", lambda _md, limit=10: [{"id": 1, "limit": limit}])
    monkeypatch.setattr(repair_cli, "rollback_operation", lambda _core, op: {"status": "ok", "op": op})

    monkeypatch.setattr(sys, "argv", ["repair_cli.py", "status"])
    assert repair_cli.main() == 0
    assert '"status": "ok"' in capsys.readouterr().out

    monkeypatch.setattr(sys, "argv", ["repair_cli.py", "history", "--limit", "3"])
    assert repair_cli.main() == 0
    assert '"limit": 3' in capsys.readouterr().out

    monkeypatch.setattr(sys, "argv", ["repair_cli.py", "rollback", "--op", "x1"])
    assert repair_cli.main() == 0
    assert '"op": "x1"' in capsys.readouterr().out


def test_repair_cli_plan_and_run_apply(monkeypatch, capsys) -> None:
    monkeypatch.setattr(repair_cli, "MemoryCore", _DummyCore)

    def _build(*_args, **kwargs):
        return {"status": "plan", "mode": kwargs.get("mode", "dry-run"), "r3_approved": False}

    monkeypatch.setattr(repair_cli, "build_repair_plan", _build)
    monkeypatch.setattr(
        repair_cli,
        "run_repair_plan",
        lambda _core, plan, mode="dry-run": {"status": "ran", "mode": mode, "approved": plan.get("r3_approved")},
    )

    monkeypatch.setattr(sys, "argv", ["repair_cli.py", "plan", "--domain", "memory"])
    assert repair_cli.main() == 0
    out = capsys.readouterr().out
    assert '"status": "plan"' in out

    monkeypatch.setattr(sys, "argv", ["repair_cli.py", "run", "--apply", "--approve-r3"])
    assert repair_cli.main() == 0
    out = capsys.readouterr().out
    assert '"status": "ran"' in out
    assert '"approved": true' in out.lower()

