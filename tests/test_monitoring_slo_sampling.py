from __future__ import annotations

from pathlib import Path

from ms8.engine_core.monitoring import MemoryMonitoring


def _mk_monitor(tmp_path: Path) -> MemoryMonitoring:
    cfg = {
        "workspace_dir": tmp_path,
        "memory_dir": tmp_path / "memory",
        "settings": {
            "memory": {
                "monitoring": {
                    "slo": {
                        "capture_rate_min": 0.85,
                        "capture_rate_min_samples": 30,
                        "injection_rate_min": 0.80,
                        "injection_rate_min_samples": 10,
                        "duplicate_drop_rate_max": 0.20,
                        "backup_success_rate_min": 1.0,
                        "restore_drill_success_rate_min": 1.0,
                        "shadow_replay_success_rate_min": 0.80,
                        "shadow_spool_pending_max": 50,
                        "shadow_checkpoint_ok_rate_min": 0.95,
                    }
                }
            }
        },
    }
    return MemoryMonitoring(cfg)


def test_slo_exempts_capture_and_injection_under_sample(tmp_path: Path) -> None:
    mon = _mk_monitor(tmp_path)
    rates = {
        "capture_rate": 0.0,
        "injection_rate": 0.0,
        "auto_total_entries": 3,
        "injection_events": 1,
        "duplicate_drop_rate": 0.0,
        "backup_success_rate": 1.0,
        "restore_drill_success_rate": 1.0,
    }
    slo = mon._evaluate_slo(rates=rates, shadow_stats={"spool_pending": 0, "verify_ok_rate": 1.0}, maintenance_stats={})
    assert slo["checks"]["capture_rate"] is True
    assert slo["checks"]["injection_rate"] is True
    assert slo["actuals"]["capture_rate_under_sample"] is True
    assert slo["actuals"]["injection_rate_under_sample"] is True
