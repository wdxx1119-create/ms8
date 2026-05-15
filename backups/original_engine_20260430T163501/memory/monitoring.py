"""Lightweight monitoring for memory subsystem health and task visibility."""
# Subdomain: ops (logical taxonomy; path unchanged)
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict

try:
    from .metrics_contract import core_metric_snapshot, metric_contract
    from .file_write_guard import atomic_write_json, atomic_write_text, guarded_file_write
except Exception:  # pragma: no cover - compatibility for direct file-loader tests
    from memory.metrics_contract import core_metric_snapshot, metric_contract
    from memory.file_write_guard import atomic_write_json, atomic_write_text, guarded_file_write


class MemoryMonitoring:
    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config
        self.workspace = config["workspace_dir"]
        self.memory_dir = config["memory_dir"]

    def _tail_jsonl(self, path: Path, limit: int = 50) -> list[dict]:
        if not path.exists():
            return []
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines()[-limit:]:
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
        return rows

    def _alert_log_path(self) -> Path:
        mon_cfg = self.config["settings"]["memory"].get("monitoring", {})
        alerts_cfg = mon_cfg.get("alerts", {})
        raw = alerts_cfg.get("alert_log_file", self.memory_dir / "alerts.jsonl")
        p = Path(raw)
        if not p.is_absolute():
            p = self.workspace / p
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            with guarded_file_write(p):
                p.write_text("", encoding="utf-8")
        return p

    def _append_alert(self, code: str, message: str, severity: str = "warning", extra: dict | None = None) -> dict:
        payload = {
            "timestamp": self._utc_now().isoformat(),
            "code": code,
            "severity": severity,
            "message": message,
            "extra": extra or {},
        }
        path = self._alert_log_path()
        with guarded_file_write(path):
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return payload

    def _last_alert_time(self, code: str) -> datetime | None:
        rows = self._tail_jsonl(self._alert_log_path(), limit=500)
        for row in reversed(rows):
            if str(row.get("code", "")) != code:
                continue
            return self._to_aware(str(row.get("timestamp", "")))
        return None

    def _alert_with_cooldown(self, code: str, message: str, severity: str, cooldown_hours: int, extra: dict | None = None) -> dict | None:
        last = self._last_alert_time(code)
        if last is not None and self._utc_now() - last < timedelta(hours=max(1, cooldown_hours)):
            return None
        return self._append_alert(code, message, severity=severity, extra=extra)

    def _load_auto_entries(self) -> list[dict]:
        auto_log = self.memory_dir / "auto_memory_log.json"
        if not auto_log.exists():
            return []
        try:
            obj = json.loads(auto_log.read_text(encoding="utf-8"))
            entries = obj.get("entries", [])
            return entries if isinstance(entries, list) else []
        except Exception:
            return []

    def _admission_route_distribution(self, limit: int = 400) -> Dict[str, int]:
        path = self.memory_dir / "auto_memory_pipeline.log"
        rows = self._tail_jsonl(path, limit=limit)
        dist: Dict[str, int] = {}
        for row in rows:
            route = ""
            trace = row.get("trace")
            # Legacy shape: trace is a list of stage dicts.
            if isinstance(trace, list):
                for stage in trace:
                    if not isinstance(stage, dict):
                        continue
                    if stage.get("stage") == "admission":
                        payload = stage.get("payload", {})
                        if isinstance(payload, dict):
                            route = str(payload.get("route", ""))
                        break
            # Current shape: trace is an object with events[].
            if not route and isinstance(trace, dict):
                events = trace.get("events", [])
                if isinstance(events, list):
                    for ev in events:
                        if not isinstance(ev, dict):
                            continue
                        if ev.get("stage") == "admission":
                            detail = ev.get("detail", {})
                            if isinstance(detail, dict):
                                route = str(detail.get("route", ""))
                            break
            if not route and isinstance(row.get("admission"), dict):
                route = str(row["admission"].get("route", ""))
            if not route:
                continue
            dist[route] = dist.get(route, 0) + 1
        return dist

    def _maintenance_policy_stats(self, limit: int = 300) -> Dict[str, Any]:
        path = self.memory_dir / "maintenance_policy_log.jsonl"
        rows = self._tail_jsonl(path, limit=limit)
        total = len(rows)
        success = 0
        failed = 0
        actions: Dict[str, int] = {}
        shadow_replay = {
            "runs": 0,
            "ok_runs": 0,
            "partial_runs": 0,
            "failed_runs": 0,
            "replayed_total": 0,
            "skipped_total": 0,
            "failed_total": 0,
            "remaining_last": 0,
            "last_run_at": "",
            "last_status": "",
        }
        for row in rows:
            action = str(row.get("action", "unknown"))
            actions[action] = actions.get(action, 0) + 1
            result = row.get("result", {})
            status = str(result.get("status", "") if isinstance(result, dict) else "")
            if status in {"success", "ok"}:
                success += 1
            elif status.startswith("error") or status == "failed":
                failed += 1
            if action == "shadow_replay_spool" and isinstance(result, dict):
                shadow_replay["runs"] += 1
                shadow_replay["last_status"] = status
                shadow_replay["last_run_at"] = str(row.get("timestamp", ""))
                shadow_replay["replayed_total"] += int(result.get("replayed", 0) or 0)
                shadow_replay["skipped_total"] += int(result.get("skipped", 0) or 0)
                shadow_replay["failed_total"] += int(result.get("failed", 0) or 0)
                shadow_replay["remaining_last"] = int(result.get("remaining", 0) or 0)
                if status in {"success", "ok"}:
                    shadow_replay["ok_runs"] += 1
                elif status == "partial":
                    shadow_replay["partial_runs"] += 1
                elif status.startswith("error") or status == "failed":
                    shadow_replay["failed_runs"] += 1
        return {
            "file": str(path),
            "total_runs": total,
            "success_runs": success,
            "failed_runs": failed,
            "actions": actions,
            "shadow_replay": shadow_replay,
        }


    def _self_check_stats(self) -> Dict[str, Any]:
        reports_dir = self.memory_dir / "reports"
        latest = reports_dir / "self_check_latest.json"
        history_dir = reports_dir / "self_check_history"
        cfg = self.config["settings"]["memory"].get("self_check", {})
        heartbeat_path = Path(str(cfg.get("heartbeat_path", "/tmp/ocma_self_check_heartbeat")))
        now = self._utc_now()

        stats: Dict[str, Any] = {
            "latest_exists": latest.exists(),
            "latest_path": str(latest),
            "history_count": len(list(history_dir.glob("*.json"))) if history_dir.exists() else 0,
            "latest_age_minutes": None,
            "latest_exit_code": None,
            "latest_level": "",
            "heartbeat_exists": heartbeat_path.exists(),
            "heartbeat_path": str(heartbeat_path),
            "heartbeat_age_minutes": None,
            "stale": False,
        }

        mon_alerts = self.config["settings"]["memory"].get("monitoring", {}).get("alerts", {})
        stale_threshold_hours = float(mon_alerts.get("self_check_stale_hours", cfg.get("watchdog_interval_hours", 2)) or 2)
        stale_minutes = max(1.0, stale_threshold_hours * 60.0)

        if latest.exists():
            try:
                payload = json.loads(latest.read_text(encoding="utf-8"))
                summary = payload.get("summary", {}) if isinstance(payload.get("summary", {}), dict) else {}
                finished = self._to_aware(str(payload.get("finished_at", "")))
                if finished is not None:
                    age = (now - finished).total_seconds() / 60.0
                    stats["latest_age_minutes"] = round(max(0.0, age), 2)
                stats["latest_exit_code"] = int(summary.get("exit_code", 2) or 2)
                stats["latest_level"] = str(payload.get("requested_level", "") or "")
            except Exception:
                pass

        if heartbeat_path.exists():
            try:
                mtime = datetime.fromtimestamp(heartbeat_path.stat().st_mtime, tz=timezone.utc)
                age = (now - mtime).total_seconds() / 60.0
                stats["heartbeat_age_minutes"] = round(max(0.0, age), 2)
            except Exception:
                pass

        age_candidates = [x for x in [stats.get("latest_age_minutes"), stats.get("heartbeat_age_minutes")] if isinstance(x, (int, float))]
        if age_candidates and min(age_candidates) > stale_minutes:
            stats["stale"] = True
        return stats

    def _threshold_suggestion_stats(self) -> Dict[str, Any]:
        pending_file = self.memory_dir / "threshold_suggestions_pending.json"
        approval_log = self.memory_dir / "threshold_suggestions_approval_log.jsonl"
        items = []
        last_generated = None
        last_applied = None
        if pending_file.exists():
            try:
                payload = json.loads(pending_file.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    raw_items = payload.get("items", [])
                    if isinstance(raw_items, list):
                        items = [x for x in raw_items if isinstance(x, dict)]
                    last_generated = payload.get("last_generated_at")
                    last_applied = payload.get("last_applied_at")
            except Exception:
                pass
        pending = sum(1 for x in items if str(x.get("status", "pending")) == "pending")
        approved = sum(1 for x in items if str(x.get("status", "")) == "approved")
        rejected = sum(1 for x in items if str(x.get("status", "")) == "rejected")
        approvals = self._tail_jsonl(approval_log, limit=200)
        return {
            "pending_file": str(pending_file),
            "approval_log_file": str(approval_log),
            "total_items": len(items),
            "pending_count": pending,
            "approved_count": approved,
            "rejected_count": rejected,
            "last_generated_at": last_generated,
            "last_applied_at": last_applied,
            "recent_approval_events": len(approvals),
            "last_approval_event": approvals[-1] if approvals else None,
        }

    def _feedback_distribution(self, limit_recent: int = 100) -> dict:
        kb_cfg = self.config["settings"]["memory"].get("knowledge_control", {})
        raw = kb_cfg.get("feedback_log_file", "memory/knowledge_feedback.jsonl")
        p = Path(raw)
        if not p.is_absolute():
            p = self.workspace / p
        rows = self._tail_jsonl(p, limit=20000)
        total = len(rows)
        recent = rows[-max(1, int(limit_recent)) :] if rows else []

        def _dist(data: list[dict], tier_key: str = "tier", trust_key: str = "trust") -> dict:
            tier: dict[str, int] = {}
            trust: dict[str, int] = {}
            for r in data:
                t = str(r.get(tier_key, "unknown"))
                tr = str(r.get(trust_key, "unknown"))
                tier[t] = tier.get(t, 0) + 1
                trust[tr] = trust.get(tr, 0) + 1
            return {"tier": tier, "trust": trust, "count": len(data)}

        result = {
            "overall": _dist(rows),
            "recent_window": _dist(recent),
            "recent_window_size": int(limit_recent),
            "file": str(p),
            "total_rows": total,
        }
        rb_cfg = kb_cfg.get("feedback_rebalance", {})
        if bool(rb_cfg.get("enabled", True)):
            rb_raw = rb_cfg.get("output_file", "memory/knowledge_feedback_rebalanced.jsonl")
            rbp = Path(rb_raw)
            if not rbp.is_absolute():
                rbp = self.workspace / rbp
            rb_rows = self._tail_jsonl(rbp, limit=max(500, int(limit_recent) * 3))
            if rb_rows:
                result["effective_recent_window"] = _dist(
                    rb_rows,
                    tier_key="effective_tier",
                    trust_key="effective_trust",
                )
                result["effective_file"] = str(rbp)
        return result



    def _review_backlog_stats(self) -> Dict[str, Any]:
        path = self.memory_dir / "auto_memory_review_queue.jsonl"
        rows = self._tail_jsonl(path, limit=20000)
        pending = 0
        for row in rows:
            if str(row.get("decision", "pending")) == "pending":
                pending += 1
        return {"file": str(path), "total": len(rows), "pending": pending}

    def _compression_freshness(self) -> Dict[str, Any]:
        report_dir = self.memory_dir / "compression_reports"
        if not report_dir.exists():
            return {"exists": False, "hours_since_last": None, "last_report": ""}
        files = sorted(report_dir.glob("compression_*.json"))
        if not files:
            return {"exists": True, "hours_since_last": None, "last_report": ""}
        last = files[-1]
        ts = self._utc_now()
        try:
            payload = json.loads(last.read_text(encoding="utf-8"))
            raw = str(payload.get("timestamp", ""))
            if raw:
                dt = self._to_aware(raw)
                if dt is not None:
                    ts = dt
        except Exception:
            pass
        hours = round(max(0.0, (self._utc_now() - ts).total_seconds() / 3600), 2)
        return {"exists": True, "hours_since_last": hours, "last_report": str(last)}

    def _shadow_runtime_stats(self) -> Dict[str, Any]:
        shadow_dir = self.memory_dir / "security" / "shadow_data"
        spool = shadow_dir / "shadow_spool.jsonl"
        verify = shadow_dir / "shadow_verify.jsonl"
        manifest = shadow_dir / "seal_manifest.json"
        health_report = shadow_dir / "shadow_health_report_latest.json"

        pending = 0
        replayed = 0
        corrupt = 0
        if spool.exists():
            for line in spool.read_text(encoding="utf-8", errors="ignore").splitlines():
                raw = line.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except Exception:
                    corrupt += 1
                    continue
                if bool(row.get("replayed", False)):
                    replayed += 1
                else:
                    pending += 1

        verify_rows = self._tail_jsonl(verify, limit=300)
        verify_total = len(verify_rows)
        verify_ok = sum(1 for r in verify_rows if bool(r.get("ok", False)))
        verify_ok_rate = round(verify_ok / verify_total, 4) if verify_total else 1.0

        sealed = False
        seal_level = "hard"
        if manifest.exists():
            try:
                obj = json.loads(manifest.read_text(encoding="utf-8"))
                sealed = bool(obj.get("sealed", False))
                seal_level = str(obj.get("seal_level", "hard") or "hard")
            except Exception:
                pass
        shadow_health_ok = None
        shadow_health_generated_at = ""
        if health_report.exists():
            try:
                h = json.loads(health_report.read_text(encoding="utf-8"))
                shadow_health_ok = bool(h.get("ok", False))
                shadow_health_generated_at = str(h.get("generated_at", ""))
            except Exception:
                shadow_health_ok = None

        return {
            "shadow_dir": str(shadow_dir),
            "spool_pending": pending,
            "spool_replayed": replayed,
            "spool_corrupt_lines": corrupt,
            "verify_total": verify_total,
            "verify_ok": verify_ok,
            "verify_ok_rate": verify_ok_rate,
            "sealed": sealed,
            "seal_level": seal_level,
            "shadow_health_report_exists": health_report.exists(),
            "shadow_health_ok": shadow_health_ok,
            "shadow_health_generated_at": shadow_health_generated_at,
        }

    def _shadow_startup_integrity_stats(self, window_hours: int = 24, limit: int = 5000) -> Dict[str, Any]:
        shadow_dir = self.memory_dir / "security" / "shadow_data"
        events = shadow_dir / "shadow_events.jsonl"
        rows = self._tail_jsonl(events, limit=max(100, int(limit)))
        now = self._utc_now()
        window = max(1, int(window_hours))
        begin = now - timedelta(hours=window)
        total = 0
        ok_count = 0
        fail_count = 0
        signatures: Dict[str, int] = {}
        last_ts = ""
        last_ok: bool | None = None
        for row in rows:
            if not isinstance(row, dict):
                continue
            if str(row.get("source", "")) != "shadow:startup_integrity":
                continue
            ts = self._to_aware(str(row.get("ts", "")))
            if ts is None or ts < begin:
                continue
            total += 1
            ok = bool(row.get("ok", False))
            last_ok = ok
            last_ts = str(row.get("ts", ""))
            if ok:
                ok_count += 1
            else:
                fail_count += 1
            meta = row.get("metadata", {})
            sig = ""
            if isinstance(meta, dict):
                sig = str(meta.get("signature", "")).strip()
            if not sig:
                sig = "unknown"
            signatures[sig] = signatures.get(sig, 0) + 1
        return {
            "file": str(events),
            "window_hours": window,
            "window_begin": begin.isoformat(),
            "events_total": total,
            "ok_count": ok_count,
            "fail_count": fail_count,
            "distinct_signatures": len(signatures),
            "top_signatures": sorted(
                [{"signature": k, "count": v} for k, v in signatures.items()],
                key=lambda x: int(x.get("count", 0)),
                reverse=True,
            )[:5],
            "last_event_ts": last_ts,
            "last_event_ok": last_ok,
        }

    def _compute_rates(self, auto_entries: list[dict], usage_rows: list[dict], maintenance: dict) -> dict:
        def _is_real_auto_entry(e: dict) -> bool:
            status = str(e.get("status", ""))
            source = str(e.get("source", ""))
            if status == "session_sync":
                return False
            if source.startswith("verify_canary:") or source.startswith("test"):
                return False
            return True

        real_auto = [e for e in auto_entries if _is_real_auto_entry(e)]
        total_auto = len(real_auto)
        success_auto = sum(1 for e in real_auto if str(e.get("status", "")) == "success")
        dropped_dup = 0
        dropped_total = 0
        for e in real_auto:
            dropped = e.get("dropped", [])
            if isinstance(dropped, list) and dropped:
                dropped_total += 1
                reasons = " ".join(str(x.get("reason", "")) for x in dropped if isinstance(x, dict)).lower()
                if "duplicate" in reasons:
                    dropped_dup += 1

        real_usage = [r for r in usage_rows if not str(r.get("query", "")).startswith("verify_canary:")]
        injection_events = len(real_usage)
        injected_events = sum(1 for r in real_usage if int(r.get("injected_count", r.get("count", 0)) or 0) > 0)
        capture_rate = round(success_auto / total_auto, 4) if total_auto else 0.0
        injection_rate = round(injected_events / injection_events, 4) if injection_events else 0.0
        # Primary SLO metric: duplicate-related drop share over all real auto-memory inputs.
        duplicate_drop_rate = round(dropped_dup / total_auto, 4) if total_auto else 0.0
        # Diagnostic metric: among dropped entries, how many are duplicate-driven.
        duplicate_drop_rate_of_dropped = round(dropped_dup / dropped_total, 4) if dropped_total else 0.0

        backup_ok = 1.0 if str(maintenance.get("last_backup_at", "")).strip() else 0.0
        restore_drill_ok = 1.0 if str(maintenance.get("last_restore_drill_at", "")).strip() else 0.0

        return {
            "capture_rate": capture_rate,
            "injection_rate": injection_rate,
            "duplicate_drop_rate": duplicate_drop_rate,
            "duplicate_drop_rate_of_dropped": duplicate_drop_rate_of_dropped,
            "auto_dropped_entries": dropped_total,
            "auto_duplicate_drop_entries": dropped_dup,
            "backup_success_rate": backup_ok,
            "auto_total_entries": total_auto,
            "auto_success_entries": success_auto,
            "injection_events": injection_events,
            "injected_events": injected_events,
            "restore_drill_success_rate": restore_drill_ok,
        }

    def _evaluate_slo(self, rates: dict, shadow_stats: Dict[str, Any], maintenance_stats: Dict[str, Any]) -> dict:
        mon_cfg = self.config["settings"]["memory"].get("monitoring", {})
        slo = mon_cfg.get("slo", {})
        capture_min = float(slo.get("capture_rate_min", 0.85))
        injection_min = float(slo.get("injection_rate_min", 0.80))
        dup_max = float(slo.get("duplicate_drop_rate_max", 0.20))
        backup_min = float(slo.get("backup_success_rate_min", 1.0))
        restore_drill_min = float(slo.get("restore_drill_success_rate_min", 1.0))
        replay_min = float(slo.get("shadow_replay_success_rate_min", 0.80))
        spool_pending_max = int(slo.get("shadow_spool_pending_max", 50))
        checkpoint_ok_min = float(slo.get("shadow_checkpoint_ok_rate_min", 0.95))
        sr = maintenance_stats.get("shadow_replay", {}) if isinstance(maintenance_stats.get("shadow_replay", {}), dict) else {}
        runs = int(sr.get("runs", 0) or 0)
        ok_runs = int(sr.get("ok_runs", 0) or 0)
        replay_rate = round(ok_runs / runs, 4) if runs else 1.0
        pending_now = int(shadow_stats.get("spool_pending", 0) or 0)
        checkpoint_ok_rate = float(shadow_stats.get("verify_ok_rate", 1.0) or 1.0)
        checks = {
            "capture_rate": rates.get("capture_rate", 0.0) >= capture_min,
            "injection_rate": rates.get("injection_rate", 0.0) >= injection_min,
            "duplicate_drop_rate": rates.get("duplicate_drop_rate", 0.0) <= dup_max,
            "backup_success_rate": rates.get("backup_success_rate", 0.0) >= backup_min,
            "restore_drill_success_rate": rates.get("restore_drill_success_rate", 0.0) >= restore_drill_min,
            "shadow_replay_success_rate": replay_rate >= replay_min,
            "shadow_spool_pending": pending_now <= spool_pending_max,
            "shadow_checkpoint_ok_rate": checkpoint_ok_rate >= checkpoint_ok_min,
        }
        return {
            "targets": {
                "capture_rate_min": capture_min,
                "injection_rate_min": injection_min,
                "duplicate_drop_rate_max": dup_max,
                "backup_success_rate_min": backup_min,
                "restore_drill_success_rate_min": restore_drill_min,
                "shadow_replay_success_rate_min": replay_min,
                "shadow_spool_pending_max": spool_pending_max,
                "shadow_checkpoint_ok_rate_min": checkpoint_ok_min,
            },
            "actuals": {
                "shadow_replay_success_rate": replay_rate,
                "shadow_spool_pending": pending_now,
                "shadow_checkpoint_ok_rate": checkpoint_ok_rate,
            },
            "checks": checks,
            "all_ok": all(checks.values()),
        }

    def _detect_anomalies(self, snapshot: Dict[str, Any], auto_entries: list[dict]) -> list[dict]:
        mon_cfg = self.config["settings"]["memory"].get("monitoring", {})
        alerts_cfg = mon_cfg.get("alerts", {})
        if not bool(alerts_cfg.get("enabled", True)):
            return []
        cooldown = int(alerts_cfg.get("alert_cooldown_hours", 2))
        no_new_hours = int(alerts_cfg.get("no_new_memory_hours", 6))
        emitted: list[dict] = []

        # No new memory in too long.
        if auto_entries:
            latest = auto_entries[-1]
            ts_raw = str(latest.get("timestamp", ""))
            ts = self._to_aware(ts_raw)
            if ts and self._utc_now() - ts > timedelta(hours=max(1, no_new_hours)):
                alert = self._alert_with_cooldown(
                    "no_new_memory",
                    f"No new memory entries for > {no_new_hours} hours",
                    severity="warning",
                    cooldown_hours=cooldown,
                    extra={"last_entry_at": ts_raw},
                )
                if alert:
                    emitted.append(alert)

        rates = snapshot.get("rates", {})
        slo = snapshot.get("slo", {})
        checks = slo.get("checks", {})
        if not bool(checks.get("capture_rate", True)):
            alert = self._alert_with_cooldown(
                "capture_rate_breach",
                "Capture rate below SLO target",
                severity="critical",
                cooldown_hours=cooldown,
                extra={"capture_rate": rates.get("capture_rate"), "target": slo.get("targets", {}).get("capture_rate_min")},
            )
            if alert:
                emitted.append(alert)
        if not bool(checks.get("injection_rate", True)):
            alert = self._alert_with_cooldown(
                "injection_rate_breach",
                "Injection rate below SLO target",
                severity="warning",
                cooldown_hours=cooldown,
                extra={"injection_rate": rates.get("injection_rate"), "target": slo.get("targets", {}).get("injection_rate_min")},
            )
            if alert:
                emitted.append(alert)

        review_stats = snapshot.get("review_backlog", {}) if isinstance(snapshot.get("review_backlog", {}), dict) else {}
        backlog_pending = int(review_stats.get("pending", 0) or 0)
        backlog_th = int(alerts_cfg.get("review_backlog_pending_threshold", 120))
        if backlog_pending >= backlog_th:
            alert = self._alert_with_cooldown(
                "review_backlog_high",
                "Pending review backlog exceeds threshold",
                severity="warning",
                cooldown_hours=cooldown,
                extra={"pending": backlog_pending, "threshold": backlog_th},
            )
            if alert:
                emitted.append(alert)

        comp = snapshot.get("compression_freshness", {}) if isinstance(snapshot.get("compression_freshness", {}), dict) else {}
        stale_hours = comp.get("hours_since_last", None)
        stale_th = int(alerts_cfg.get("compression_stale_hours", 48))
        if isinstance(stale_hours, (int, float)) and stale_hours >= stale_th:
            alert = self._alert_with_cooldown(
                "compression_stale",
                "Compression report is stale",
                severity="warning",
                cooldown_hours=cooldown,
                extra={"hours_since_last": stale_hours, "threshold": stale_th, "last_report": comp.get("last_report", "")},
            )
            if alert:
                emitted.append(alert)

        mp = snapshot.get("maintenance_policy_stats", {}) if isinstance(snapshot.get("maintenance_policy_stats", {}), dict) else {}
        sr = mp.get("shadow_replay", {}) if isinstance(mp.get("shadow_replay", {}), dict) else {}
        replay_failed = int(sr.get("failed_total", 0) or 0)
        replay_remaining = int(sr.get("remaining_last", 0) or 0)
        replay_failed_th = int(alerts_cfg.get("shadow_replay_failed_threshold", 1))
        replay_remaining_th = int(alerts_cfg.get("shadow_replay_remaining_threshold", 20))
        if replay_failed >= replay_failed_th:
            alert = self._alert_with_cooldown(
                "shadow_replay_failed",
                "Shadow spool replay has failures",
                severity="warning",
                cooldown_hours=cooldown,
                extra={
                    "failed_total": replay_failed,
                    "threshold": replay_failed_th,
                    "last_status": sr.get("last_status", ""),
                    "last_run_at": sr.get("last_run_at", ""),
                },
            )
            if alert:
                emitted.append(alert)
        if replay_remaining >= replay_remaining_th:
            alert = self._alert_with_cooldown(
                "shadow_replay_backlog",
                "Shadow spool replay backlog is high",
                severity="warning",
                cooldown_hours=cooldown,
                extra={
                    "remaining_last": replay_remaining,
                    "threshold": replay_remaining_th,
                    "last_status": sr.get("last_status", ""),
                    "last_run_at": sr.get("last_run_at", ""),
                },
            )
            if alert:
                emitted.append(alert)
        shadow_stats = snapshot.get("shadow_runtime_stats", {}) if isinstance(snapshot.get("shadow_runtime_stats", {}), dict) else {}
        verify_ok_rate = float(shadow_stats.get("verify_ok_rate", 1.0) or 1.0)
        verify_threshold = float(alerts_cfg.get("shadow_checkpoint_low_threshold", 0.90))
        if verify_ok_rate < verify_threshold:
            alert = self._alert_with_cooldown(
                "shadow_checkpoint_low",
                "Shadow checkpoint integrity rate is below threshold",
                severity="warning",
                cooldown_hours=cooldown,
                extra={"verify_ok_rate": verify_ok_rate, "threshold": verify_threshold},
            )
            if alert:
                emitted.append(alert)
        scs = snapshot.get("self_check_stats", {}) if isinstance(snapshot.get("self_check_stats", {}), dict) else {}
        if bool(scs.get("stale", False)):
            alert = self._alert_with_cooldown(
                "self_check_stale",
                "Self-check heartbeat/report appears stale",
                severity="warning",
                cooldown_hours=cooldown,
                extra={
                    "latest_age_minutes": scs.get("latest_age_minutes"),
                    "heartbeat_age_minutes": scs.get("heartbeat_age_minutes"),
                    "latest_exit_code": scs.get("latest_exit_code"),
                },
            )
            if alert:
                emitted.append(alert)

        startup_integrity = snapshot.get("shadow_startup_integrity", {}) if isinstance(snapshot.get("shadow_startup_integrity", {}), dict) else {}
        si_events = int(startup_integrity.get("events_total", 0) or 0)
        si_fail = int(startup_integrity.get("fail_count", 0) or 0)
        si_window = int(startup_integrity.get("window_hours", 24) or 24)
        si_min_events = int(alerts_cfg.get("shadow_startup_integrity_min_events", 20) or 20)
        si_fail_count_th = int(alerts_cfg.get("shadow_startup_integrity_fail_count_threshold", 10) or 10)
        si_fail_ratio_th = float(alerts_cfg.get("shadow_startup_integrity_fail_ratio_threshold", 0.30) or 0.30)
        si_fail_ratio = round(si_fail / max(1, si_events), 4) if si_events > 0 else 0.0
        if si_events >= si_min_events and si_fail >= si_fail_count_th:
            alert = self._alert_with_cooldown(
                "shadow_startup_integrity_fail_high",
                "Shadow startup integrity failures exceed threshold",
                severity="warning",
                cooldown_hours=cooldown,
                extra={
                    "window_hours": si_window,
                    "events_total": si_events,
                    "fail_count": si_fail,
                    "threshold": si_fail_count_th,
                },
            )
            if alert:
                emitted.append(alert)
        if si_events >= si_min_events and si_fail_ratio >= si_fail_ratio_th:
            alert = self._alert_with_cooldown(
                "shadow_startup_integrity_ratio_high",
                "Shadow startup integrity failure ratio is high",
                severity="warning",
                cooldown_hours=cooldown,
                extra={
                    "window_hours": si_window,
                    "events_total": si_events,
                    "fail_count": si_fail,
                    "fail_ratio": si_fail_ratio,
                    "threshold": si_fail_ratio_th,
                },
            )
            if alert:
                emitted.append(alert)
        return emitted

    def write_daily_health_report(self, snapshot: Dict[str, Any]) -> Dict[str, str]:
        mon_cfg = self.config["settings"]["memory"].get("monitoring", {})
        json_path = Path(mon_cfg.get("daily_report_file", self.memory_dir / "health_report_latest.json"))
        md_path = Path(mon_cfg.get("daily_report_markdown", self.memory_dir / "health_report_latest.md"))
        if not json_path.is_absolute():
            json_path = self.workspace / json_path
        if not md_path.is_absolute():
            md_path = self.workspace / md_path
        json_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.parent.mkdir(parents=True, exist_ok=True)

        with guarded_file_write(json_path):
            atomic_write_json(json_path, snapshot, ensure_ascii=False, indent=2)

        slo = snapshot.get("slo", {})
        rates = snapshot.get("rates", {})
        lines = [
            "# Memory Health Report",
            f"- timestamp: {snapshot.get('timestamp', '')}",
            f"- capture_rate: {rates.get('capture_rate', 0.0)}",
            f"- injection_rate: {rates.get('injection_rate', 0.0)}",
            f"- duplicate_drop_rate: {rates.get('duplicate_drop_rate', 0.0)}",
            f"- duplicate_drop_rate_of_dropped: {rates.get('duplicate_drop_rate_of_dropped', 0.0)}",
            f"- backup_success_rate: {rates.get('backup_success_rate', 0.0)}",
            f"- restore_drill_success_rate: {rates.get('restore_drill_success_rate', 0.0)}",
            f"- slo_all_ok: {slo.get('all_ok', False)}",
        ]
        mp = snapshot.get("maintenance_policy_stats", {}) if isinstance(snapshot.get("maintenance_policy_stats", {}), dict) else {}
        sr = mp.get("shadow_replay", {}) if isinstance(mp.get("shadow_replay", {}), dict) else {}
        lines.extend(
            [
                f"- shadow_replay_runs: {sr.get('runs', 0)}",
                f"- shadow_replay_replayed_total: {sr.get('replayed_total', 0)}",
                f"- shadow_replay_failed_total: {sr.get('failed_total', 0)}",
                f"- shadow_replay_remaining_last: {sr.get('remaining_last', 0)}",
                f"- shadow_replay_last_status: {sr.get('last_status', '')}",
            ]
        )
        slo_actuals = snapshot.get("slo", {}).get("actuals", {}) if isinstance(snapshot.get("slo", {}), dict) else {}
        lines.extend(
            [
                f"- shadow_slo_replay_success_rate: {slo_actuals.get('shadow_replay_success_rate', 1.0)}",
                f"- shadow_slo_spool_pending: {slo_actuals.get('shadow_spool_pending', 0)}",
                f"- shadow_slo_checkpoint_ok_rate: {slo_actuals.get('shadow_checkpoint_ok_rate', 1.0)}",
            ]
        )
        srt = snapshot.get("shadow_runtime_stats", {}) if isinstance(snapshot.get("shadow_runtime_stats", {}), dict) else {}
        sis = snapshot.get("shadow_startup_integrity", {}) if isinstance(snapshot.get("shadow_startup_integrity", {}), dict) else {}
        scs = snapshot.get("self_check_stats", {}) if isinstance(snapshot.get("self_check_stats", {}), dict) else {}
        lines.extend(
            [
                f"- shadow_spool_pending: {srt.get('spool_pending', 0)}",
                f"- shadow_spool_replayed: {srt.get('spool_replayed', 0)}",
                f"- shadow_verify_ok_rate: {srt.get('verify_ok_rate', 1.0)}",
                f"- shadow_sealed: {srt.get('sealed', False)}",
                f"- shadow_seal_level: {srt.get('seal_level', '')}",
                f"- shadow_health_report_exists: {srt.get('shadow_health_report_exists', False)}",
                f"- shadow_health_ok: {srt.get('shadow_health_ok', None)}",
                f"- shadow_health_generated_at: {srt.get('shadow_health_generated_at', '')}",
                f"- startup_integrity_window_hours: {sis.get('window_hours', 24)}",
                f"- startup_integrity_events_total: {sis.get('events_total', 0)}",
                f"- startup_integrity_fail_count: {sis.get('fail_count', 0)}",
                f"- startup_integrity_ok_count: {sis.get('ok_count', 0)}",
                f"- startup_integrity_distinct_signatures: {sis.get('distinct_signatures', 0)}",
                f"- startup_integrity_last_event_ts: {sis.get('last_event_ts', '')}",
                f"- startup_integrity_last_event_ok: {sis.get('last_event_ok', None)}",
                f"- self_check_latest_age_minutes: {scs.get('latest_age_minutes', None)}",
                f"- self_check_latest_exit_code: {scs.get('latest_exit_code', None)}",
                f"- self_check_latest_level: {scs.get('latest_level', '')}",
                f"- self_check_heartbeat_age_minutes: {scs.get('heartbeat_age_minutes', None)}",
                f"- self_check_stale: {scs.get('stale', False)}",
            ]
        )
        with guarded_file_write(md_path):
            atomic_write_text(md_path, "\n".join(lines) + "\n", encoding="utf-8")
        return {"json": str(json_path), "markdown": str(md_path)}

    def status(self) -> Dict[str, Any]:
        mon_cfg = self.config["settings"]["memory"].get("monitoring", {})
        if not bool(mon_cfg.get("enabled", True)):
            return {"timestamp": self._utc_now().isoformat(), "enabled": False}

        learning_log = self.memory_dir / "learning_task_log.jsonl"
        usage_log = self.memory_dir / "memory_usage_log.jsonl"
        maintenance_state = self.memory_dir / "maintenance_state.json"

        learning_rows = self._tail_jsonl(learning_log)
        usage_rows = self._tail_jsonl(usage_log)

        maintenance = {}
        if maintenance_state.exists():
            try:
                maintenance = json.loads(maintenance_state.read_text(encoding="utf-8"))
            except Exception:
                maintenance = {}

        auto_entries = self._load_auto_entries()
        rates = self._compute_rates(auto_entries, usage_rows, maintenance)

        snapshot = {
            "timestamp": self._utc_now().isoformat(),
            "enabled": True,
            "learning_log_exists": learning_log.exists(),
            "learning_last_event": learning_rows[-1] if learning_rows else None,
            "memory_injection_events": len(usage_rows),
            "memory_injection_last": usage_rows[-1] if usage_rows else None,
            "maintenance_state": maintenance,
            "auto_memory_entries": len(auto_entries),
            "rates": rates,
            "backup_dir_exists": (self.memory_dir / "backups").exists(),
            "subagent_log_files": len(list((self.memory_dir / "subagent_logs").glob("*.log"))) if (self.memory_dir / "subagent_logs").exists() else 0,
        }
        snapshot["feedback_distribution"] = self._feedback_distribution(
            limit_recent=int(mon_cfg.get("feedback_recent_window", 100))
        )
        snapshot["admission_route_distribution"] = self._admission_route_distribution(
            limit=int(mon_cfg.get("admission_recent_window", 400))
        )
        snapshot["maintenance_policy_stats"] = self._maintenance_policy_stats(
            limit=int(mon_cfg.get("maintenance_policy_recent_window", 300))
        )
        snapshot["self_check_stats"] = self._self_check_stats()
        snapshot["shadow_runtime_stats"] = self._shadow_runtime_stats()
        snapshot["shadow_startup_integrity"] = self._shadow_startup_integrity_stats(
            window_hours=int(mon_cfg.get("shadow_startup_integrity_window_hours", 24)),
            limit=int(mon_cfg.get("shadow_startup_integrity_tail_limit", 5000)),
        )
        snapshot["slo"] = self._evaluate_slo(
            rates,
            snapshot["shadow_runtime_stats"],
            snapshot["maintenance_policy_stats"],
        )
        snapshot["threshold_suggestion_stats"] = self._threshold_suggestion_stats()
        snapshot["review_backlog"] = self._review_backlog_stats()
        snapshot["compression_freshness"] = self._compression_freshness()
        snapshot["core_metric_contract"] = metric_contract()
        snapshot["core_metrics"] = core_metric_snapshot(rates, snapshot["slo"])
        snapshot["alerts_emitted"] = self._detect_anomalies(snapshot, auto_entries)
        snapshot["alerts_recent"] = self._tail_jsonl(self._alert_log_path(), limit=20)
        snapshot["report_paths"] = self.write_daily_health_report(snapshot)
        return snapshot
    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _to_aware(ts_text: str) -> datetime | None:
        try:
            raw = str(ts_text or "").strip()
            if not raw:
                return None
            if raw.endswith("Z"):
                raw = raw[:-1] + "+00:00"
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
