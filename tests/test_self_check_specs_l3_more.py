from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ms8.engine_core.maintenance.self_check import check_specs as cs


class _Core:
    def __init__(self, memory_dir: Path, workspace_dir: Path) -> None:
        self.config = {
            "memory_dir": str(memory_dir),
            "workspace_dir": str(workspace_dir),
            "settings": {"memory": {"security": {"shadow": {"backup_dir": str(memory_dir / "backup")}}}},
        }

    def shadow_status(self) -> dict:
        return {
            "manifest_signature_valid": True,
            "sealed": False,
            "spool_pending": 0,
            "history": [],
        }

    def shadow_verify(self) -> dict:
        return {"ok": True}

    def shadow_health(self) -> dict:
        return {"ok": True, "status": "ok", "mode": "normal", "issues": []}


def test_l3_manifest_and_checkpoint_statuses(tmp_path: Path) -> None:
    core = _Core(tmp_path, tmp_path)

    assert cs._check_l3_manifest_signature(core, {})["status"] == "pass"
    assert cs._check_l3_checkpoint_verify(core, {})["status"] == "pass"

    core_bad = _Core(tmp_path, tmp_path)
    core_bad.shadow_status = lambda: {"manifest_signature_valid": False}  # type: ignore[method-assign]
    core_bad.shadow_verify = lambda: {"ok": False, "reason": "broken"}  # type: ignore[method-assign]
    assert cs._check_l3_manifest_signature(core_bad, {})["status"] == "fail"
    assert cs._check_l3_checkpoint_verify(core_bad, {})["status"] == "fail"


def test_l3_backup_sensitive_and_spool(tmp_path: Path) -> None:
    mem = tmp_path / "mem"
    ws = tmp_path / "ws"
    mem.mkdir(parents=True, exist_ok=True)
    ws.mkdir(parents=True, exist_ok=True)
    core = _Core(mem, ws)

    # backup freshness warn path
    state = mem / "maintenance_state.json"
    old = (datetime.now(timezone.utc) - timedelta(hours=60)).isoformat()
    state.write_text(json.dumps({"last_backup_at": old}, ensure_ascii=False), encoding="utf-8")
    out_backup = cs._check_l3_backup_freshness(core, {})
    assert out_backup["status"] == "warn"

    # sensitive scan warn path
    (ws / "MEMORY.md").write_text("token: github_pat_abcdefghijklmnopqrstuvwxyz12345", encoding="utf-8")
    out_sensitive = cs._check_l3_sensitive_scan(core, {})
    assert out_sensitive["status"] == "warn"

    # spool backlog warn/fail by stubbing status payload
    now = datetime.now(timezone.utc)
    core.shadow_status = lambda: {  # type: ignore[method-assign]
        "sealed": False,
        "spool_pending": 3,
        "spool_oldest_pending_ts": (now - timedelta(hours=8)).isoformat(),
    }
    assert cs._check_l3_spool_backlog(core, {})["status"] == "warn"

    core.shadow_status = lambda: {  # type: ignore[method-assign]
        "sealed": False,
        "spool_pending": 2,
        "spool_oldest_pending_ts": (now - timedelta(hours=30)).isoformat(),
    }
    assert cs._check_l3_spool_backlog(core, {})["status"] == "fail"


def test_l3_seal_history_and_closed_loop(tmp_path: Path) -> None:
    mem = tmp_path / "mem"
    ws = tmp_path / "ws"
    mem.mkdir(parents=True, exist_ok=True)
    ws.mkdir(parents=True, exist_ok=True)
    core = _Core(mem, ws)

    now = datetime.now(timezone.utc)
    core.shadow_status = lambda: {  # type: ignore[method-assign]
        "history": [
            {"event": "seal", "ts": (now - timedelta(hours=1)).isoformat()},
            {"event": "seal", "ts": (now - timedelta(hours=2)).isoformat()},
            {"event": "seal", "ts": (now - timedelta(hours=3)).isoformat()},
        ]
    }
    assert cs._check_l3_seal_history(core, {})["status"] == "warn"

    log = mem / "maintenance_policy_log.jsonl"
    rows = [
        {"action": "a1", "trigger_reason": "cron", "result": {"status": "ok", "report_paths": ["x"]}},
        {"action": "a2", "trigger_reason": "", "result": {"status": "ok"}},
        {"action": "a3", "trigger_reason": "manual", "result": {}},
    ]
    log.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    out = cs._check_l3_closed_loop_evidence(core, {})
    assert out["status"] in {"warn", "fail"}

