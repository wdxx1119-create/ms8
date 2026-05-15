from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
import json
from datetime import datetime, timezone


@dataclass
class MaintenanceAction:
    action: str
    reason: str
    enabled: bool = True
    priority: int = 100


def should_compress_memory(stats: Dict[str, Any]) -> bool:
    return int(stats.get("memory_md_lines", 0)) > int(stats.get("memory_md_lines_threshold", 500))


def should_cleanup_test_pollution(stats: Dict[str, Any]) -> bool:
    return float(stats.get("test_pollution_ratio", 0.0)) >= float(stats.get("test_pollution_ratio_threshold", 0.15))


def should_cleanup_test_memories(stats: Dict[str, Any]) -> bool:
    return int(stats.get("auto_check_test_residual_count", 0) or 0) > 0


def should_backfill_record_ids(stats: Dict[str, Any]) -> bool:
    return int(stats.get("missing_record_ids", 0)) > 0


def should_repair_semantic_cache(stats: Dict[str, Any]) -> bool:
    return int(stats.get("semantic_dense_missing", 0)) >= int(stats.get("semantic_dense_missing_threshold", 20))


def should_rebalance_feedback(stats: Dict[str, Any]) -> bool:
    return float(stats.get("feedback_dominant_ratio", 0.0)) >= float(stats.get("feedback_dominant_ratio_threshold", 0.9))


def should_trigger_batch_review(stats: Dict[str, Any]) -> bool:
    pending = int(stats.get("review_backlog_pending", 0) or 0)
    hard_th = int(stats.get("review_backlog_pending_threshold", 80) or 80)
    soft_th = int(stats.get("review_backlog_pending_soft_threshold", 50) or 50)
    stale_hours = float(stats.get("review_backlog_stale_hours", 0.0) or 0.0)
    stale_th = float(stats.get("review_backlog_stale_hours_threshold", 24.0) or 24.0)
    return pending >= hard_th or (pending >= soft_th and stale_hours >= stale_th)


def should_generate_threshold_suggestions(stats: Dict[str, Any]) -> bool:
    return float(stats.get("feedback_recent_count", 0)) >= float(stats.get("feedback_recent_min_for_suggestions", 120))


def should_auto_seal(stats: Dict[str, Any]) -> bool:
    consecutive = int(stats.get("write_fail_consecutive", 0) or 0)
    recent_30s = int(stats.get("write_fail_recent_30s", 0) or 0)
    daily_limit = int(stats.get("auto_seal_daily_limit", 5) or 5)
    triggered_today = int(stats.get("auto_seal_triggered_today", 0) or 0)
    if triggered_today >= max(1, daily_limit):
        return False
    return consecutive >= 3 and recent_30s >= 2


def should_auto_replay(stats: Dict[str, Any]) -> bool:
    return (not bool(stats.get("shadow_sealed", False))) and int(stats.get("shadow_spool_pending", 0) or 0) > 0


def should_auto_recover(stats: Dict[str, Any]) -> bool:
    if not bool(stats.get("shadow_sealed", False)):
        return False
    sealed_hours = float(stats.get("shadow_sealed_hours", 0.0) or 0.0)
    # Keep first gate simple and safe: only recover after long enough sealed duration and no fresh write failures.
    return sealed_hours >= 1.0 and int(stats.get("write_fail_consecutive", 0) or 0) == 0


def should_reset_checkpoint(stats: Dict[str, Any]) -> bool:
    mismatch = bool(stats.get("shadow_checkpoint_mismatch", False))
    seal_count_24h = int(stats.get("shadow_seal_events_24h", 0) or 0)
    return mismatch and seal_count_24h >= 5


def should_shadow_drill(stats: Dict[str, Any]) -> bool:
    hours_since = float(stats.get("shadow_drill_hours_since_last", 10**9) or 10**9)
    return hours_since >= 24 * 7


def should_replay_shadow_spool(stats: Dict[str, Any]) -> bool:
    pending = int(stats.get("shadow_spool_pending", 0) or 0)
    th = int(stats.get("shadow_spool_pending_threshold", 1) or 1)
    sealed = bool(stats.get("shadow_sealed", False))
    return sealed or pending >= th


def should_archive_shadow_spool(stats: Dict[str, Any]) -> bool:
    replayed = int(stats.get("shadow_spool_replayed", 0) or 0)
    th = int(stats.get("shadow_spool_archive_threshold", 40) or 40)
    return replayed >= th


def should_self_heal_shadow(stats: Dict[str, Any]) -> bool:
    return int(stats.get("shadow_corrupt_line_count", 0) or 0) > 0


def should_sync_shadow_backup(stats: Dict[str, Any]) -> bool:
    sealed = bool(stats.get("shadow_sealed", False))
    if sealed:
        return False
    hours_since = float(stats.get("shadow_backup_hours_since_last", 10**9) or 10**9)
    interval = float(stats.get("shadow_backup_interval_hours", 24) or 24)
    return hours_since >= max(1.0, interval)


def should_run_self_check_l1(stats: Dict[str, Any]) -> bool:
    if not bool(stats.get("self_check_enabled", True)):
        return False
    if bool(stats.get("self_check_in_progress", False)):
        return False
    age_minutes = float(stats.get("self_check_l1_latest_age_minutes", 10**9) or 10**9)
    interval_minutes = float(stats.get("self_check_l1_interval_minutes", 30) or 30)
    return age_minutes >= max(1.0, interval_minutes)


def should_run_self_check_l2l3(stats: Dict[str, Any]) -> bool:
    if not bool(stats.get("self_check_enabled", True)):
        return False
    if bool(stats.get("self_check_in_progress", False)):
        return False
    age_minutes = float(stats.get("self_check_l2l3_latest_age_minutes", 10**9) or 10**9)
    interval_hours = float(stats.get("self_check_l2l3_interval_hours", 24) or 24)
    return age_minutes >= max(1.0, interval_hours * 60.0)


def should_run_self_check_l4(stats: Dict[str, Any]) -> bool:
    if not bool(stats.get("self_check_enabled", True)):
        return False
    if bool(stats.get("self_check_in_progress", False)):
        return False
    age_minutes = float(stats.get("self_check_l4_latest_age_minutes", 10**9) or 10**9)
    interval_hours = float(stats.get("self_check_l4_interval_hours", 168) or 168)
    return age_minutes >= max(1.0, interval_hours * 60.0)


def should_run_self_repair(stats: Dict[str, Any]) -> bool:
    if bool(stats.get("self_check_in_progress", False)):
        return False
    if not bool(stats.get("self_repair_enabled", True)):
        return False
    fail_count = int(stats.get("self_check_fail_count", 0) or 0)
    error_count = int(stats.get("self_check_error_count", 0) or 0)
    warn_count = int(stats.get("self_check_warn_count", 0) or 0)
    if fail_count > 0 or error_count > 0:
        return True
    if int(stats.get("alerts_recent_critical", 0) or 0) > 0:
        return True
    # Optional conservative mode: allow warn-triggered dry-run planning only.
    return bool(stats.get("self_repair_on_warn", False)) and warn_count > 0


def should_force_self_check_from_alerts(stats: Dict[str, Any]) -> bool:
    critical = int(stats.get("alerts_recent_critical", 0) or 0)
    error = int(stats.get("alerts_recent_error", 0) or 0)
    return critical > 0 or error >= 2


def build_policy_actions(stats: Dict[str, Any]) -> List[MaintenanceAction]:
    actions: List[MaintenanceAction] = []
    if should_force_self_check_from_alerts(stats):
        actions.append(MaintenanceAction("self_check_l2l3", "alert_bridge_triggered", priority=10))
    # Self-check: run full chain first when overdue, else quick L1.
    if should_run_self_check_l2l3(stats):
        actions.append(MaintenanceAction("self_check_l2l3", "self_check_overdue", priority=12))
    elif should_run_self_check_l1(stats):
        actions.append(MaintenanceAction("self_check_l1", "self_check_interval_due", priority=13))
    if should_run_self_check_l4(stats):
        actions.append(MaintenanceAction("self_check_l4", "self_check_l4_interval_due", priority=14))
    if should_run_self_repair(stats):
        actions.append(MaintenanceAction("self_repair_auto", "self_check_issues_detected", priority=16))

    if should_compress_memory(stats):
        actions.append(MaintenanceAction("trigger_weekly_compression", "memory_md_over_threshold", priority=20))
    if should_cleanup_test_memories(stats):
        actions.append(MaintenanceAction("cleanup_test_memories", "auto_check_test_residual_detected", priority=24))
    if should_cleanup_test_pollution(stats):
        actions.append(MaintenanceAction("purge_test_memory_data", "test_pollution_ratio_high", priority=30))
    if should_backfill_record_ids(stats):
        actions.append(MaintenanceAction("backfill_auto_memory_record_ids", "record_id_missing_detected", priority=35))
    if should_auto_seal(stats):
        actions.append(MaintenanceAction("shadow_auto_seal", "write_fail_streak_triggered", priority=36))
    if should_auto_recover(stats):
        actions.append(MaintenanceAction("shadow_auto_recover", "shadow_sealed_recover_ready", priority=36))
    if should_auto_replay(stats):
        actions.append(MaintenanceAction("shadow_auto_replay", "shadow_spool_pending_auto", priority=37))
    if should_reset_checkpoint(stats):
        actions.append(MaintenanceAction("shadow_reset_checkpoint", "checkpoint_mismatch_with_seal_storm", priority=37))
    if should_replay_shadow_spool(stats):
        reason = "shadow_sealed_replay" if bool(stats.get("shadow_sealed", False)) else "shadow_spool_pending"
        actions.append(MaintenanceAction("shadow_replay_spool", reason, priority=37))
    if should_self_heal_shadow(stats):
        actions.append(MaintenanceAction("shadow_startup_self_heal", "shadow_corrupt_lines_detected", priority=38))
    if should_shadow_drill(stats):
        actions.append(MaintenanceAction("shadow_recovery_drill", "shadow_weekly_dry_run_due", priority=38))
    if should_archive_shadow_spool(stats):
        actions.append(MaintenanceAction("shadow_archive_spool", "shadow_replayed_spool_archive_ready", priority=39))
    if should_sync_shadow_backup(stats):
        actions.append(MaintenanceAction("shadow_sync_verified_backup", "shadow_backup_interval_due", priority=40))
    if should_repair_semantic_cache(stats):
        actions.append(MaintenanceAction("repair_semantic_cache", "semantic_dense_missing_high", priority=50))
    if should_rebalance_feedback(stats):
        actions.append(MaintenanceAction("rebalance_feedback_distribution", "feedback_distribution_skewed", priority=60))
    if should_trigger_batch_review(stats):
        pending = int(stats.get("review_backlog_pending", 0) or 0)
        hard_th = int(stats.get("review_backlog_pending_threshold", 80) or 80)
        stale_hours = float(stats.get("review_backlog_stale_hours", 0.0) or 0.0)
        stale_th = float(stats.get("review_backlog_stale_hours_threshold", 24.0) or 24.0)
        reason = "review_backlog_high" if pending >= hard_th else "review_backlog_stale"
        if pending < hard_th and stale_hours < stale_th:
            reason = "review_backlog_guard"
        actions.append(MaintenanceAction("trigger_batch_review", reason, priority=55))
    if should_generate_threshold_suggestions(stats):
        actions.append(MaintenanceAction("generate_threshold_suggestions", "feedback_window_ready", priority=65))
    actions.sort(key=lambda x: x.priority)
    dedup: List[MaintenanceAction] = []
    seen: set[str] = set()
    for action in actions:
        if action.action in seen:
            continue
        seen.add(action.action)
        dedup.append(action)
    return dedup


def gather_policy_stats(workspace_dir: Path, policy_cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    memory_dir = workspace_dir / "memory"
    memory_md = workspace_dir / "MEMORY.md"
    stats: Dict[str, Any] = {
        "memory_md_lines": 0,
        "memory_md_lines_threshold": 500,
        "test_pollution_ratio": 0.0,
        "test_pollution_ratio_threshold": 0.15,
        "missing_record_ids": 0,
        "semantic_dense_missing": 0,
        "semantic_dense_missing_threshold": 20,
        "feedback_dominant_ratio": 0.0,
        "feedback_dominant_ratio_threshold": 0.9,
        "review_backlog_pending": 0,
        "review_backlog_pending_threshold": 80,
        "review_backlog_pending_soft_threshold": 50,
        "review_backlog_stale_hours": 0.0,
        "review_backlog_stale_hours_threshold": 24.0,
        "feedback_recent_count": 0,
        "feedback_recent_min_for_suggestions": 120,
        "shadow_spool_pending": 0,
        "shadow_spool_pending_threshold": 1,
        "shadow_spool_replayed": 0,
        "shadow_spool_archive_threshold": 40,
        "shadow_sealed": False,
        "shadow_corrupt_line_count": 0,
        "shadow_backup_hours_since_last": 10**9,
        "shadow_backup_interval_hours": 24,
        "write_fail_consecutive": 0,
        "write_fail_recent_30s": 0,
        "auto_seal_daily_limit": 5,
        "auto_seal_triggered_today": 0,
        "shadow_sealed_hours": 0.0,
        "shadow_checkpoint_mismatch": False,
        "shadow_seal_events_24h": 0,
        "shadow_drill_hours_since_last": 10**9,
        "auto_check_test_residual_count": 0,
        "self_check_enabled": True,
        "self_check_in_progress": False,
        "self_check_l1_interval_minutes": 30,
        "self_check_l2l3_interval_hours": 24,
        "self_check_l4_interval_hours": 168,
        "self_check_latest_age_minutes": 10**9,
        "self_check_l1_latest_age_minutes": 10**9,
        "self_check_l2l3_latest_age_minutes": 10**9,
        "self_check_l4_latest_age_minutes": 10**9,
        "self_check_latest_exit_code": 2,
        "self_check_latest_level": "",
        "self_check_warn_count": 0,
        "self_check_fail_count": 0,
        "self_check_error_count": 0,
        "self_repair_enabled": True,
        "self_repair_on_warn": False,
        "alerts_window_minutes": 30,
        "alerts_recent_critical": 0,
        "alerts_recent_error": 0,
        "alerts_recent_warning": 0,
    }
    cfg = policy_cfg or {}
    cfg_thresholds = dict(cfg.get("thresholds", {}))
    cfg_self = dict(cfg.get("self_check", {}))

    for key in (
        "memory_md_lines_threshold",
        "test_pollution_ratio_threshold",
        "semantic_dense_missing_threshold",
        "feedback_dominant_ratio_threshold",
        "review_backlog_pending_threshold",
        "review_backlog_pending_soft_threshold",
        "review_backlog_stale_hours_threshold",
        "feedback_recent_min_for_suggestions",
        "shadow_spool_pending_threshold",
        "shadow_spool_archive_threshold",
        "shadow_backup_interval_hours",
        "alerts_window_minutes",
    ):
        if key in cfg_thresholds:
            stats[key] = cfg_thresholds[key]

    if "enabled" in cfg_self:
        stats["self_check_enabled"] = bool(cfg_self.get("enabled"))
    if "l1_interval_minutes" in cfg_self:
        stats["self_check_l1_interval_minutes"] = float(cfg_self.get("l1_interval_minutes") or 30)
    if "l2l3_interval_hours" in cfg_self:
        stats["self_check_l2l3_interval_hours"] = float(cfg_self.get("l2l3_interval_hours") or 24)
    if "l4_interval_hours" in cfg_self:
        stats["self_check_l4_interval_hours"] = float(cfg_self.get("l4_interval_hours") or 168)
    if "self_repair_enabled" in cfg_self:
        stats["self_repair_enabled"] = bool(cfg_self.get("self_repair_enabled"))
    if "self_repair_on_warn" in cfg_self:
        stats["self_repair_on_warn"] = bool(cfg_self.get("self_repair_on_warn"))
    stats["auto_seal_daily_limit"] = int(cfg.get("auto_seal_daily_limit", 5) or 5)

    if memory_md.exists():
        stats["memory_md_lines"] = len(memory_md.read_text(encoding="utf-8", errors="ignore").splitlines())

    records = memory_dir / "auto_memory_records.jsonl"
    if records.exists():
        total = 0
        testish = 0
        missing_id = 0
        auto_check_residual = 0
        for line in records.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not line.strip():
                continue
            total += 1
            low = line.lower()
            if any(x in low for x in ["verify_canary", "verification", "smoke", "样本"]):
                testish += 1
            try:
                row = json.loads(line)
                rid = str(row.get("id", "") or row.get("meta", {}).get("id", ""))
                if not rid:
                    missing_id += 1
                meta = row.get("meta", {}) if isinstance(row.get("meta", {}), dict) else {}
                if bool(meta.get("_auto_check_test", False)):
                    auto_check_residual += 1
            except Exception:
                continue
        stats["test_pollution_ratio"] = round(testish / max(1, total), 4)
        stats["missing_record_ids"] = missing_id
        stats["auto_check_test_residual_count"] = auto_check_residual

    semantic_cache = memory_dir / "semantic_cache.json"
    if semantic_cache.exists():
        try:
            payload = json.loads(semantic_cache.read_text(encoding="utf-8"))
            missing = 0
            if isinstance(payload, dict):
                for _, item in payload.items():
                    if isinstance(item, dict) and item.get("dense") is None:
                        missing += 1
            stats["semantic_dense_missing"] = missing
        except Exception:
            pass

    review_file = memory_dir / "auto_memory_review_queue.jsonl"
    if review_file.exists():
        pending = 0
        for line in review_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if str(row.get("decision", "pending")) == "pending":
                pending += 1
        stats["review_backlog_pending"] = pending
        try:
            mtime = datetime.fromtimestamp(review_file.stat().st_mtime, tz=timezone.utc)
            stats["review_backlog_stale_hours"] = max(0.0, (datetime.now(timezone.utc) - mtime).total_seconds() / 3600.0)
        except Exception:
            stats["review_backlog_stale_hours"] = 0.0

    feedback_file = memory_dir / "knowledge_feedback.jsonl"
    if feedback_file.exists():
        total = 0
        dominant_bucket = {}
        for line in feedback_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not line.strip():
                continue
            total += 1
            try:
                row = json.loads(line)
                key = f"{row.get('tier','')}|{row.get('trust','')}"
                dominant_bucket[key] = dominant_bucket.get(key, 0) + 1
            except Exception:
                continue
        top = max(dominant_bucket.values()) if dominant_bucket else 0
        stats["feedback_dominant_ratio"] = round(top / max(1, total), 4)
        stats["feedback_recent_count"] = total

    # Shadow survival layer stats.
    shadow_dir = memory_dir / "security" / "shadow_data"
    spool_file = shadow_dir / "shadow_spool.jsonl"
    manifest_file = shadow_dir / "seal_manifest.json"
    if spool_file.exists():
        pending = 0
        replayed = 0
        corrupt = 0
        for line in spool_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            raw = line.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except Exception:
                corrupt += 1
                continue
            if not bool(row.get("replayed", False)):
                pending += 1
            else:
                replayed += 1
        stats["shadow_spool_pending"] = pending
        stats["shadow_spool_replayed"] = replayed
        stats["shadow_corrupt_line_count"] = corrupt
    if manifest_file.exists():
        try:
            obj = json.loads(manifest_file.read_text(encoding="utf-8"))
            stats["shadow_sealed"] = bool(obj.get("sealed", False))
            sealed_at = str(obj.get("sealed_at", "") or "").strip()
            if sealed_at:
                raw = sealed_at[:-1] + "+00:00" if sealed_at.endswith("Z") else sealed_at
                dt = datetime.fromisoformat(raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                else:
                    dt = dt.astimezone(timezone.utc)
                stats["shadow_sealed_hours"] = max(
                    0.0,
                    (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0,
                )
            history = obj.get("history", [])
            seal_count_24h = 0
            if isinstance(history, list):
                cutoff = datetime.now(timezone.utc).timestamp() - 24 * 3600
                for item in history:
                    if not isinstance(item, dict):
                        continue
                    if str(item.get("event", "")).lower() != "seal":
                        continue
                    ts_raw = str(item.get("ts", "") or "")
                    if not ts_raw:
                        continue
                    raw2 = ts_raw[:-1] + "+00:00" if ts_raw.endswith("Z") else ts_raw
                    try:
                        t = datetime.fromisoformat(raw2)
                        if t.tzinfo is None:
                            t = t.replace(tzinfo=timezone.utc)
                        else:
                            t = t.astimezone(timezone.utc)
                        if t.timestamp() >= cutoff:
                            seal_count_24h += 1
                    except Exception:
                        continue
            stats["shadow_seal_events_24h"] = seal_count_24h
        except Exception:
            pass

    verify_file = shadow_dir / "shadow_verify.jsonl"
    if verify_file.exists():
        try:
            rows = verify_file.read_text(encoding="utf-8", errors="ignore").splitlines()[-200:]
            mismatch = 0
            for ln in rows:
                raw = ln.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except Exception:
                    continue
                ok = bool(row.get("ok", True))
                if not ok:
                    mismatch += 1
            stats["shadow_checkpoint_mismatch"] = mismatch > 0
        except Exception:
            pass

    write_fail_file = memory_dir / "write_fail_state.json"
    if write_fail_file.exists():
        try:
            wf = json.loads(write_fail_file.read_text(encoding="utf-8"))
            if isinstance(wf, dict):
                stats["write_fail_consecutive"] = int(wf.get("consecutive_failures", 0) or 0)
                stats["write_fail_recent_30s"] = int(wf.get("recent_failures_30s", 0) or 0)
        except Exception:
            pass

    backup_manifest = Path.home() / ".shadow_backup" / "backup_manifest.json"
    if backup_manifest.exists():
        try:
            rows = json.loads(backup_manifest.read_text(encoding="utf-8"))
            if isinstance(rows, list) and rows:
                last = rows[-1]
                ts_raw = str(last.get("ts", "") or "")
                if ts_raw:
                    if ts_raw.endswith("Z"):
                        ts_raw = ts_raw[:-1] + "+00:00"
                    dt = datetime.fromisoformat(ts_raw)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    else:
                        dt = dt.astimezone(timezone.utc)
                    stats["shadow_backup_hours_since_last"] = max(
                        0.0,
                        (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0,
                    )
        except Exception:
            pass

    # Count today's auto-seal runs from policy log for daily cap.
    policy_log = memory_dir / "maintenance_policy_log.jsonl"
    if policy_log.exists():
        today = datetime.now(timezone.utc).date().isoformat()
        auto_seal_today = 0
        last_drill_at = None
        for ln in policy_log.read_text(encoding="utf-8", errors="ignore").splitlines()[-3000:]:
            raw = ln.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except Exception:
                continue
            act = str(row.get("action", "") or "")
            ts_raw = str(row.get("timestamp", "") or "")
            if not ts_raw:
                continue
            if ts_raw.endswith("Z"):
                ts_raw = ts_raw[:-1] + "+00:00"
            try:
                dt = datetime.fromisoformat(ts_raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                else:
                    dt = dt.astimezone(timezone.utc)
            except Exception:
                continue
            if act == "shadow_auto_seal" and dt.date().isoformat() == today:
                auto_seal_today += 1
            if act == "shadow_recovery_drill":
                if last_drill_at is None or dt > last_drill_at:
                    last_drill_at = dt
        stats["auto_seal_triggered_today"] = auto_seal_today
        if last_drill_at is not None:
            stats["shadow_drill_hours_since_last"] = max(
                0.0,
                (datetime.now(timezone.utc) - last_drill_at).total_seconds() / 3600.0,
            )

    # Self-check freshness stats.
    reports_dir = memory_dir / "reports"
    in_progress = reports_dir / "check_in_progress.json"
    latest = reports_dir / "self_check_latest.json"
    stats["self_check_in_progress"] = in_progress.exists()
    if latest.exists():
        try:
            payload = json.loads(latest.read_text(encoding="utf-8"))
            finished = str(payload.get("finished_at", "") or "")
            summary = payload.get("summary", {}) if isinstance(payload.get("summary", {}), dict) else {}
            exit_code = int(summary.get("exit_code", 2) or 2)
            level = str(payload.get("requested_level", "") or "")
            stats["self_check_warn_count"] = int(summary.get("warn", 0) or 0)
            stats["self_check_fail_count"] = int(summary.get("fail", 0) or 0)
            stats["self_check_error_count"] = int(summary.get("error", 0) or 0)
            if finished:
                raw = finished[:-1] + "+00:00" if finished.endswith("Z") else finished
                dt = datetime.fromisoformat(raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                else:
                    dt = dt.astimezone(timezone.utc)
                stats["self_check_latest_age_minutes"] = max(
                    0.0,
                    (datetime.now(timezone.utc) - dt).total_seconds() / 60.0,
                )
            stats["self_check_latest_exit_code"] = exit_code
            stats["self_check_latest_level"] = level
        except Exception:
            pass

    # Per-level freshness from history to avoid one level starving others.
    try:
        history_dir = reports_dir / "self_check_history"
        latest_l1_minutes = None
        latest_l2l3_minutes = None
        latest_l4_minutes = None
        if history_dir.exists():
            for hp in sorted(history_dir.glob("*.json"), reverse=True):
                try:
                    h = json.loads(hp.read_text(encoding="utf-8"))
                except Exception:
                    continue
                req_level = str(h.get("requested_level", "")).upper()
                ts_raw = str(h.get("finished_at", h.get("started_at", "")) or "")
                if not ts_raw:
                    continue
                if ts_raw.endswith("Z"):
                    ts_raw = ts_raw[:-1] + "+00:00"
                dt = datetime.fromisoformat(ts_raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                else:
                    dt = dt.astimezone(timezone.utc)
                age = max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 60.0)
                if req_level == "L1" and latest_l1_minutes is None:
                    latest_l1_minutes = age
                elif req_level in {"FULL", "FULL_PLUS"} and latest_l2l3_minutes is None:
                    latest_l2l3_minutes = age
                elif req_level == "L4" and latest_l4_minutes is None:
                    latest_l4_minutes = age
                if (
                    latest_l1_minutes is not None
                    and latest_l2l3_minutes is not None
                    and latest_l4_minutes is not None
                ):
                    break
        if latest_l1_minutes is not None:
            stats["self_check_l1_latest_age_minutes"] = latest_l1_minutes
        if latest_l2l3_minutes is not None:
            stats["self_check_l2l3_latest_age_minutes"] = latest_l2l3_minutes
        if latest_l4_minutes is not None:
            stats["self_check_l4_latest_age_minutes"] = latest_l4_minutes
    except Exception:
        pass

    # Alert bridge stats (monitoring -> self_check/self_repair trigger).
    try:
        alerts_file = memory_dir / "alerts.jsonl"
        if alerts_file.exists():
            now = datetime.now(timezone.utc)
            window_minutes = max(1.0, float(stats.get("alerts_window_minutes", 30) or 30))
            cutoff = now.timestamp() - window_minutes * 60.0
            c = e = w = 0
            for ln in alerts_file.read_text(encoding="utf-8", errors="ignore").splitlines()[-1500:]:
                raw = ln.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except Exception:
                    continue
                ts_raw = str(row.get("timestamp", "") or "")
                if not ts_raw:
                    continue
                if ts_raw.endswith("Z"):
                    ts_raw = ts_raw[:-1] + "+00:00"
                try:
                    dt = datetime.fromisoformat(ts_raw)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    else:
                        dt = dt.astimezone(timezone.utc)
                except Exception:
                    continue
                if dt.timestamp() < cutoff:
                    continue
                sev = str(row.get("severity", "") or "").lower()
                if sev == "critical":
                    c += 1
                elif sev == "error":
                    e += 1
                elif sev in {"warn", "warning"}:
                    w += 1
            stats["alerts_recent_critical"] = c
            stats["alerts_recent_error"] = e
            stats["alerts_recent_warning"] = w
    except Exception:
        pass

    return stats
