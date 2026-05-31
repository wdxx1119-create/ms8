from __future__ import annotations

import json
import time
from pathlib import Path

from ms8.engine_core.maintenance.self_check import check_specs as cs


class _Core:
    def __init__(self, memory_dir: Path) -> None:
        self.config = {
            "memory_dir": str(memory_dir),
            "settings": {
                "memory": {
                    "self_check": {
                        "repair_effectiveness_fail_success_rate": 0.5,
                        "repair_effectiveness_warn_success_rate": 0.75,
                        "repair_effectiveness_warn_rollback_rate": 0.3,
                    }
                }
            },
        }


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_l2_repair_audit_health_warn_and_fail_and_ok(tmp_path: Path, monkeypatch) -> None:
    core = _Core(tmp_path)
    log_file = tmp_path / "logs" / "repair_ops_audit.jsonl"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    # malformed tail rows -> warn
    log_file.write_text('{"ok": true}\n{bad}\n', encoding="utf-8")
    out_warn = cs._check_l2_repair_audit_health(core, {})
    assert out_warn["status"] == "warn"

    # readable but not writable -> fail
    log_file.write_text('{"ok": true}\n', encoding="utf-8")
    monkeypatch.setattr(cs.os, "access", lambda *_args, **_kwargs: False)
    out_fail = cs._check_l2_repair_audit_health(core, {})
    assert out_fail["status"] == "fail"

    # writable and clean -> pass
    monkeypatch.setattr(cs.os, "access", lambda *_args, **_kwargs: True)
    out_ok = cs._check_l2_repair_audit_health(core, {})
    assert out_ok["status"] == "pass"


def test_l2_repair_lock_health_active_and_stale(tmp_path: Path) -> None:
    core = _Core(tmp_path)
    lock = tmp_path / "state" / "repair_in_progress.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("1", encoding="utf-8")

    # active lock
    lock.touch()
    out_active = cs._check_l2_repair_lock_health(core, {})
    assert out_active["status"] == "pass"

    # stale lock (> 3600s)
    old = time.time() - 4001
    lock.touch()
    # direct utime to set past mtime
    cs.os.utime(lock, (old, old))
    out_stale = cs._check_l2_repair_lock_health(core, {})
    assert out_stale["status"] == "warn"


def test_l3_repair_effectiveness_full_branches(tmp_path: Path) -> None:
    core = _Core(tmp_path)
    latest = tmp_path / "reports" / "self_check_latest.json"

    # missing -> warn
    out_missing = cs._check_l3_repair_effectiveness(core, {})
    assert out_missing["status"] == "warn"

    # unreadable -> warn
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text("{", encoding="utf-8")
    out_unreadable = cs._check_l3_repair_effectiveness(core, {})
    assert out_unreadable["status"] == "warn"

    # total <= 0 -> ok skipped
    _write_json(latest, {"repair_summary": {"window_7d": {"total": 0, "success_rate": 0.0, "rollback_rate": 0.0}}})
    out_skip = cs._check_l3_repair_effectiveness(core, {})
    assert out_skip["status"] == "pass"

    # small sample -> ok
    _write_json(latest, {"repair_summary": {"window_7d": {"total": 5, "success_rate": 0.2, "rollback_rate": 0.0}}})
    out_small = cs._check_l3_repair_effectiveness(core, {})
    assert out_small["status"] == "pass"

    # fail by low success
    _write_json(latest, {"repair_summary": {"window_7d": {"total": 20, "success_rate": 0.3, "rollback_rate": 0.0}}})
    out_fail = cs._check_l3_repair_effectiveness(core, {})
    assert out_fail["status"] == "fail"

    # warn by success below warn threshold
    _write_json(latest, {"repair_summary": {"window_7d": {"total": 20, "success_rate": 0.6, "rollback_rate": 0.1}}})
    out_warn_success = cs._check_l3_repair_effectiveness(core, {})
    assert out_warn_success["status"] == "warn"

    # warn by rollback high
    _write_json(latest, {"repair_summary": {"window_7d": {"total": 20, "success_rate": 0.9, "rollback_rate": 0.4}}})
    out_warn_rollback = cs._check_l3_repair_effectiveness(core, {})
    assert out_warn_rollback["status"] == "warn"

    # healthy -> ok
    _write_json(latest, {"repair_summary": {"window_7d": {"total": 20, "success_rate": 0.9, "rollback_rate": 0.1}}})
    out_ok = cs._check_l3_repair_effectiveness(core, {})
    assert out_ok["status"] == "pass"
