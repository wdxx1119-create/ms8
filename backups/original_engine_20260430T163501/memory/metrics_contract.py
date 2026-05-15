"""Shared core metric definitions for monitoring/governance reports."""
from __future__ import annotations

from typing import Any, Dict

CORE_METRIC_DEFINITION_VERSION = "v1"

CORE_METRIC_DEFS: Dict[str, Dict[str, Any]] = {
    "capture_rate": {
        "meaning": "Accepted auto-memory records / real auto-memory inputs.",
        "formula": "auto_success_entries / auto_total_entries",
        "source": "memory/auto_memory_log.json",
    },
    "injection_rate": {
        "meaning": "Response turns with injected memories / real response-context events.",
        "formula": "injected_events / injection_events",
        "source": "memory/memory_usage_log.jsonl",
    },
    "duplicate_drop_rate": {
        "meaning": "Duplicate-related drops over all real auto-memory inputs.",
        "formula": "duplicate_drop_events / auto_total_entries",
        "source": "memory/auto_memory_log.json",
    },
    "duplicate_drop_rate_of_dropped": {
        "meaning": "Duplicate-related drops among dropped auto-memory records (diagnostic).",
        "formula": "duplicate_drop_events / total_drop_events",
        "source": "memory/auto_memory_log.json",
    },
    "backup_success_rate": {
        "meaning": "Whether maintenance produced at least one valid backup in current window.",
        "formula": "1.0 if last_backup_at exists else 0.0",
        "source": "memory/maintenance_state.json",
    },
    "restore_drill_success_rate": {
        "meaning": "Whether restore drill has successful recent evidence.",
        "formula": "1.0 if last_restore_drill_at exists else 0.0",
        "source": "memory/maintenance_state.json",
    },
}


def metric_contract() -> Dict[str, Any]:
    return {
        "version": CORE_METRIC_DEFINITION_VERSION,
        "metrics": CORE_METRIC_DEFS,
    }


def core_metric_snapshot(rates: Dict[str, Any], slo: Dict[str, Any]) -> Dict[str, Any]:
    keys = list(CORE_METRIC_DEFS.keys())
    return {
        "definition_version": CORE_METRIC_DEFINITION_VERSION,
        "rates": {k: rates.get(k, 0.0) for k in keys},
        "targets": {k: slo.get("targets", {}).get(f"{k}_min", slo.get("targets", {}).get(f"{k}_max")) for k in keys},
        "checks": {k: bool(slo.get("checks", {}).get(k, True)) for k in keys},
        "all_ok": bool(slo.get("all_ok", False)),
    }
