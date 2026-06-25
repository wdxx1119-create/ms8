"""Background-friendly watch loop."""

from __future__ import annotations

import contextlib
import io
import logging
import time
from datetime import datetime, timezone
from urllib.parse import quote

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


def _self_check_snapshot(payload: dict) -> dict[str, object]:
    """Normalize self-check payload for concise watch logging."""
    raw = payload.get("result") if isinstance(payload.get("result"), dict) else payload
    if not isinstance(raw, dict):
        return {
            "status": "unknown",
            "pass": 0,
            "warn": 0,
            "fail": 0,
            "error": 0,
            "warn_ids": [],
            "fail_ids": [],
        }

    status_raw = str(raw.get("status", "unknown")).strip().lower()
    status = {
        "ok": "pass",
        "success": "pass",
        "healthy": "pass",
        "warning": "warn",
        "warn": "warn",
        "failed": "fail",
        "fail": "fail",
        "error": "error",
    }.get(status_raw, status_raw or "unknown")

    summary = raw.get("summary", {}) if isinstance(raw.get("summary", {}), dict) else {}
    if summary:
        warn_ids: list[str] = []
        fail_ids: list[str] = []
        results = raw.get("results", [])
        if isinstance(results, list):
            for row in results:
                if not isinstance(row, dict):
                    continue
                check_id = str(row.get("check_id", "")).strip()
                row_status = str(row.get("status", "")).strip().lower()
                if row_status == "warn" and check_id:
                    warn_ids.append(check_id)
                elif row_status in {"fail", "error"} and check_id:
                    fail_ids.append(check_id)
        return {
            "status": status,
            "pass": int(summary.get("pass", 0) or 0),
            "warn": int(summary.get("warn", 0) or 0),
            "fail": int(summary.get("fail", 0) or 0),
            "error": int(summary.get("error", 0) or 0),
            "warn_ids": warn_ids[:3],
            "fail_ids": fail_ids[:3],
        }

    counts = {"pass": 0, "warn": 0, "fail": 0, "error": 0}
    warn_ids: list[str] = []
    fail_ids: list[str] = []
    results = raw.get("results", [])
    if isinstance(results, list):
        for row in results:
            if not isinstance(row, dict):
                continue
            check_id = str(row.get("check_id", "")).strip()
            row_status = str(row.get("status", "")).strip().lower()
            if row_status in counts:
                counts[row_status] += 1
            if row_status == "warn" and check_id:
                warn_ids.append(check_id)
            elif row_status in {"fail", "error"} and check_id:
                fail_ids.append(check_id)
    return {"status": status, **counts, "warn_ids": warn_ids[:3], "fail_ids": fail_ids[:3]}


def _doctor_follow_up_actions(output: str) -> list[str]:
    actions: list[str] = []
    seen: set[str] = set()
    for raw_line in str(output or "").splitlines():
        line = raw_line.strip()
        if line.startswith("watch next:"):
            action = line.split(":", 1)[1].strip()
        elif line.startswith("watch also:"):
            action = line.split(":", 1)[1].strip()
        else:
            continue
        if not action or action in seen:
            continue
        seen.add(action)
        actions.append(action)
    return actions


def _encode_watch_actions(actions: list[str]) -> str:
    encoded = [quote(str(action).strip(), safe="") for action in actions if str(action).strip()]
    return "|".join(encoded)


def run_watch(interval_seconds: int = 1800, once: bool = False) -> int:
    ensure_runtime_dirs()
    if interval_seconds < 10:
        interval_seconds = 10
    active_window = int(max(30, interval_seconds // 6))

    while True:
        ts = datetime.now(timezone.utc).isoformat()
        doctor_buf = io.StringIO()
        with contextlib.redirect_stdout(doctor_buf):
            code = run_doctor()
        doctor_output = doctor_buf.getvalue()
        if doctor_output:
            print(doctor_output, end="")
        doctor_actions = _doctor_follow_up_actions(doctor_output)
        mem_count = count_memories()
        learning = run_daily_learning()
        kg_extract = run_kg_batch_extract(limit=20, force=False)
        tiering = run_memory_tiering()
        graph_maint = run_graph_maintenance()
        reflection = run_reflection()
        synth_auto = run_synthetic_auto_confirm()
        self_check = _self_check_snapshot(run_engine_self_check(level="L2"))
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
            f"synth_auto={synth_auto.get('ran')} "
            f"self_check={self_check.get('status', 'unknown')} "
            f"self_check_counts="
            f"{self_check.get('pass', 0)}/{self_check.get('warn', 0)}/"
            f"{self_check.get('fail', 0)}/{self_check.get('error', 0)} "
            f"absorb_risk={absorb.get('risk')} absorb_pending={absorb.get('pending_review')} "
            f"absorb_quarantine={absorb.get('quarantine')} "
            f"compression_repair={compression.get('ran')} duplicate_cluster={dedupe_status}"
        )
        warn_ids = self_check.get("warn_ids", [])
        fail_ids = self_check.get("fail_ids", [])
        if isinstance(fail_ids, list) and fail_ids:
            tick_message += f" self_check_fail_ids={','.join(str(x) for x in fail_ids)}"
        if isinstance(warn_ids, list) and warn_ids:
            tick_message += f" self_check_warn_ids={','.join(str(x) for x in warn_ids)}"
        if doctor_actions:
            tick_message += f" next_actions={_encode_watch_actions(doctor_actions[:3])}"
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
