"""Background-friendly watch loop."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from .absorb.health import absorb_health_summary
from .doctor import run_doctor
from .runtime import (
    backup_memories,
    cleanup_old_backups,
    count_memories,
    ensure_runtime_dirs,
    has_recent_activity,
    repair_compression_if_stale,
    repair_duplicates_after_compression,
    run_daily_learning,
    run_engine_self_check,
    run_graph_maintenance,
    run_kg_batch_extract,
    run_maintenance_now,
    run_maintenance_policy,
    run_memory_tiering,
    run_reflection,
    run_synthetic_auto_confirm,
)

logger = logging.getLogger(__name__)


def run_watch(interval_seconds: int = 1800, once: bool = False) -> int:
    ensure_runtime_dirs()
    if interval_seconds < 10:
        interval_seconds = 10
    active_window = int(max(30, interval_seconds // 6))

    while True:
        ts = datetime.now(timezone.utc).isoformat()
        code = run_doctor()
        mem_count = count_memories()
        learning = run_daily_learning()
        kg_extract = run_kg_batch_extract(limit=20, force=False)
        tiering = run_memory_tiering()
        graph_maint = run_graph_maintenance()
        reflection = run_reflection()
        synth_auto = run_synthetic_auto_confirm()
        self_check = run_engine_self_check(level="L2")
        absorb = absorb_health_summary()
        maintenance = run_maintenance_now(force=True)
        if not maintenance.get("ok", False):
            maintenance = run_maintenance_policy()
        compression = repair_compression_if_stale()
        dedupe = (
            repair_duplicates_after_compression()
            if compression.get("ran")
            else {"ok": True, "result": {"status": "skipped"}}
        )
        dedupe_result = dedupe.get("result")
        dedupe_status = (
            dedupe_result.get("status")
            if isinstance(dedupe_result, dict)
            else "unknown"
        )
        tick_message = (
            f"watch tick: ts={ts} status={code} memories={mem_count} "
            f"learning={learning.get('ran')} maintenance={maintenance.get('ran')} "
            f"kg_extract={kg_extract.get('ran')} tiering={tiering.get('ran')} "
            f"graph_maint={graph_maint.get('ran')} reflection={reflection.get('ran')} "
            f"synth_auto={synth_auto.get('ran')} self_check={self_check.get('status', 'n/a')} "
            f"absorb_risk={absorb.get('risk')} absorb_pending={absorb.get('pending_review')} "
            f"absorb_quarantine={absorb.get('quarantine')} "
            f"compression_repair={compression.get('ran')} duplicate_cluster={dedupe_status}"
        )
        if has_recent_activity(window_seconds=active_window):
            final_message = f"{tick_message} backup=skipped cleanup=skipped reason=recent_activity"
            logger.info(final_message)
            print(final_message)
        else:
            snapshot = backup_memories(tag="watch")
            cleanup = cleanup_old_backups(max_keep=20)
            backup_path = snapshot["path"]
            removed_count = cleanup["removed_count"]
            final_message = f"{tick_message} backup={backup_path} cleanup_removed={removed_count}"
            logger.info(final_message)
            print(final_message)
        if once:
            return code
        time.sleep(interval_seconds)
