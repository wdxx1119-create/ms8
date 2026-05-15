from __future__ import annotations

import fcntl
import json
import os
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .check_specs import (
    STATUS_ERROR,
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_WARN,
    CheckSpec,
    build_check_specs,
)
from .reporter import load_latest as reporter_load_latest
from .reporter import persist_report

SCHEMA_VERSION = "1.0"
RUNNER_VERSION = "1.0"
CHECKS_VERSION = "1.5-59"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return _utc_now().isoformat()


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
    except (TypeError, ValueError):
        return None


def _process_start_text(pid: int) -> str:
    try:
        out = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(int(pid))],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
        if out.returncode != 0:
            return ""
        return str(out.stdout or "").strip()
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        return ""


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def _emit_healthchecks_ping(config: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    cfg = config.get("settings", {}).get("memory", {}).get("self_check", {})
    if not bool(cfg.get("healthchecks_enabled", False)):
        return {"status": "disabled"}
    base_url = str(cfg.get("healthchecks_url", "") or "").strip()
    if not base_url:
        return {"status": "skipped", "reason": "missing_url"}
    fail_suffix = str(cfg.get("healthchecks_fail_suffix", "/fail") or "/fail")

    summary = report.get("summary", {}) if isinstance(report.get("summary", {}), dict) else {}
    exit_code = int(summary.get("exit_code", 2) or 2)
    ok = exit_code == 0
    url = base_url if ok else (base_url.rstrip("/") + fail_suffix)
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            code = int(getattr(resp, "status", 200) or 200)
        return {"status": "ok", "url": url, "http_status": code}
    except (OSError, ValueError, TypeError, urllib.error.URLError) as exc:
        return {"status": "error", "url": url, "error": str(exc)}


def _summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    passed = sum(1 for x in results if str(x.get("status")) == STATUS_PASS)
    warn = sum(1 for x in results if str(x.get("status")) == STATUS_WARN)
    fail = sum(1 for x in results if str(x.get("status")) == STATUS_FAIL)
    err = sum(1 for x in results if str(x.get("status")) == STATUS_ERROR)
    if fail > 0 or err > 0:
        exit_code = 2
    elif warn > 0:
        exit_code = 1
    else:
        exit_code = 0
    return {
        "total": total,
        "pass": passed,
        "warn": warn,
        "fail": fail,
        "error": err,
        "exit_code": exit_code,
    }


def _run_one(spec: CheckSpec, core: Any, ctx: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    payload: dict[str, Any] = {
        "check_id": spec.check_id,
        "level": spec.level,
        "domain": getattr(spec, "domain", "memory"),
        "status": STATUS_ERROR,
        "message": "",
        "duration_ms": 0,
        "details": {},
        "action_guide": spec.action_guide,
    }

    def _target() -> dict[str, Any]:
        return spec.fn(core, ctx)

    timer_flag = {"timeout": False}

    def _mark_timeout() -> None:
        timer_flag["timeout"] = True

    t = threading.Timer(float(spec.timeout_s), _mark_timeout)
    t.daemon = True
    t.start()
    try:
        with ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(_target)
            try:
                out = fut.result(timeout=float(spec.timeout_s))
            except FutureTimeout:
                payload["status"] = STATUS_ERROR
                payload["message"] = "timeout"
                payload["details"] = {"timeout_s": spec.timeout_s}
                return payload
        if not isinstance(out, dict):
            payload["status"] = STATUS_ERROR
            payload["message"] = "invalid check output"
            return payload
        payload["status"] = str(out.get("status", STATUS_ERROR))
        payload["message"] = str(out.get("message", ""))
        payload["details"] = dict(out.get("details", {})) if isinstance(out.get("details", {}), dict) else {}
        return payload
    except (RuntimeError, OSError, TypeError, ValueError, FutureTimeout) as exc:
        payload["status"] = STATUS_ERROR
        payload["message"] = f"exception:{exc}"
        payload["details"] = {"error": str(exc)}
        return payload
    finally:
        t.cancel()
        payload["duration_ms"] = int((time.perf_counter() - started) * 1000)
        if timer_flag["timeout"] and payload.get("status") not in {STATUS_ERROR, STATUS_FAIL}:
            payload["status"] = STATUS_ERROR
            payload["message"] = "timeout"


def _write_report_markdown(path: Path, report: dict[str, Any]) -> None:
    summary = report.get("summary", {})
    lines = [
        "# Self Check Report",
        f"- started_at: {report.get('started_at', '')}",
        f"- finished_at: {report.get('finished_at', '')}",
        f"- level: {report.get('requested_level', 'L1')}",
        f"- total: {summary.get('total', 0)}",
        f"- pass: {summary.get('pass', 0)}",
        f"- warn: {summary.get('warn', 0)}",
        f"- fail: {summary.get('fail', 0)}",
        f"- error: {summary.get('error', 0)}",
        f"- exit_code: {summary.get('exit_code', 0)}",
        "",
    ]
    for item in report.get("results", []):
        if not isinstance(item, dict):
            continue
        icon = "✅"
        if item.get("status") == STATUS_WARN:
            icon = "⚠️"
        elif item.get("status") in {STATUS_FAIL, STATUS_ERROR}:
            icon = "❌"
        lines.append(
            f"- {icon} `{item.get('check_id', '')}` [{item.get('level', '')}] "
            f"{item.get('status', '')} - {item.get('message', '')}"
        )
        if item.get("action_guide"):
            lines.append(f"  - action: {item.get('action_guide')}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_latest_report(config: dict[str, Any]) -> dict[str, Any]:
    memory_dir = Path(config["memory_dir"])
    return reporter_load_latest(memory_dir)


def run_self_check(core: Any, level: str = "L1") -> dict[str, Any]:
    config = core.config
    memory_dir = Path(config["memory_dir"])
    reports_dir = memory_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    latest_json = reports_dir / "self_check_latest.json"
    lock_path = reports_dir / "check.lock"
    progress_path = reports_dir / "check_in_progress.json"
    heartbeat_path = Path(
        config["settings"]["memory"].get("self_check", {}).get("heartbeat_path", "/tmp/ocma_self_check_heartbeat")
    )

    # Handle stale progress mark from interrupted last run.
    interrupted_prev = False
    if progress_path.exists():
        try:
            payload = json.loads(progress_path.read_text(encoding="utf-8"))
            pid = int(payload.get("pid", 0) or 0)
            recorded_start = str(payload.get("process_start", "")).strip()
            alive = pid > 0 and _pid_alive(pid)
            same_proc = alive and (recorded_start == _process_start_text(pid))
            if not same_proc:
                progress_path.unlink(missing_ok=True)
                interrupted_prev = True
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            progress_path.unlink(missing_ok=True)
            interrupted_prev = True

    lock_fp = lock_path.open("a+", encoding="utf-8")
    stale_lock_released = False
    try:
        try:
            fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            # stale lock rescue: if a stale progress file is older than 2h, try to terminate stale holder
            stale_threshold_s = int(
                config["settings"]["memory"].get("self_check", {}).get("stale_lock_seconds", 7200) or 7200
            )
            rescued = False
            stale_attempted = False
            try:
                if progress_path.exists():
                    payload = json.loads(progress_path.read_text(encoding="utf-8"))
                    started_at = _to_aware(str(payload.get("started_at", "")))
                    pid = int(payload.get("pid", 0) or 0)
                    process_start = str(payload.get("process_start", "")).strip()
                    age_s = None
                    if started_at is not None:
                        age_s = (_utc_now() - started_at).total_seconds()
                    if age_s is not None and age_s >= max(600, stale_threshold_s):
                        stale_attempted = True
                        same_proc = bool(pid > 0 and _pid_alive(pid) and process_start == _process_start_text(pid))
                        if same_proc:
                            try:
                                os.kill(pid, signal.SIGTERM)
                                time.sleep(1.0)
                                if _pid_alive(pid):
                                    os.kill(pid, signal.SIGKILL)
                            except (OSError, ValueError, TypeError) as exc:
                                print(f"[SelfCheckRunner] Failed terminating stale process {pid}: {exc}")
                        # reopen and retry lock regardless of kill outcome
                        try:
                            lock_fp.close()
                        except OSError as exc:
                            print(f"[SelfCheckRunner] Failed closing stale lock file handle: {exc}")
                        lock_fp = lock_path.open("a+", encoding="utf-8")
                        fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        rescued = True
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                rescued = False

            if not rescued:
                return {
                    "status": "skipped",
                    "reason": "check_skipped_concurrent",
                    "path": str(latest_json),
                    "stale_lock_attempted": stale_attempted,
                }
            stale_lock_released = True

        pid = os.getpid()
        progress_payload = {
            "pid": pid,
            "process_start": _process_start_text(pid),
            "started_at": _iso_now(),
            "current_check": "",
            "level": str(level or "L1").upper(),
        }
        progress_path.write_text(json.dumps(progress_payload, ensure_ascii=False, indent=2), encoding="utf-8")

        checks = build_check_specs(level=level)
        started_at_iso = _iso_now()
        results: list[dict[str, Any]] = []
        ctx: dict[str, Any] = {
            "workspace_dir": str(config["workspace_dir"]),
            "memory_dir": str(memory_dir),
        }
        for spec in checks:
            progress_payload["current_check"] = spec.check_id
            progress_path.write_text(json.dumps(progress_payload, ensure_ascii=False, indent=2), encoding="utf-8")
            row = _run_one(spec, core, ctx)
            results.append(row)

        finished_at = _iso_now()
        summary = _summarize(results)
        report: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "runner_version": RUNNER_VERSION,
            "checks_version": CHECKS_VERSION,
            "requested_level": str(level or "L1").upper(),
            "status": "ok"
            if int(summary.get("exit_code", 1)) == 0
            else ("warning" if int(summary.get("exit_code", 1)) == 1 else "failed"),
            "started_at": started_at_iso,
            "finished_at": finished_at,
            "interrupted_last_run": interrupted_prev,
            "stale_lock_force_released": stale_lock_released,
            "summary": summary,
            "results": results,
        }
        known_noncritical = config["settings"]["memory"].get("self_check", {}).get("known_noncritical_failures", [])
        if isinstance(known_noncritical, list):
            report["known_noncritical_failures"] = [str(x) for x in known_noncritical]

        report["healthchecks"] = _emit_healthchecks_ping(config, report)
        self_check_cfg = config["settings"]["memory"].get("self_check", {})
        persist_meta = persist_report(
            memory_dir,
            report,
            keep_days=int(self_check_cfg.get("history_keep_days", 30) or 30),
            keep_max=int(self_check_cfg.get("history_keep_max", 500) or 500),
            cooldown_hours=int(self_check_cfg.get("alert_cooldown_hours", 6) or 6),
            max_alerts_per_day=int(self_check_cfg.get("alert_max_per_day", 3) or 3),
        )
        report["persist"] = persist_meta

        # heartbeat independent from shadow
        try:
            heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
            heartbeat_path.write_text(_iso_now(), encoding="utf-8")
        except OSError as exc:
            print(f"[SelfCheckRunner] Failed writing heartbeat {heartbeat_path}: {exc}")

        # shadow audit event
        if getattr(core, "shadow", None):
            try:
                core.shadow.record_data(
                    action="self_check_completed",
                    source="maintenance:self_check",
                    content="self_check_completed",
                    ok=summary.get("exit_code", 1) == 0,
                    metadata={"summary": summary, "level": report["requested_level"]},
                )
            except (OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
                print(f"[SelfCheckRunner] Failed writing shadow audit event: {exc}")
        return report
    finally:
        try:
            progress_path.unlink(missing_ok=True)
        except OSError as exc:
            print(f"[SelfCheckRunner] Failed removing progress file {progress_path}: {exc}")
        try:
            fcntl.flock(lock_fp.fileno(), fcntl.LOCK_UN)
        except OSError as exc:
            print(f"[SelfCheckRunner] Failed unlocking lock file {lock_path}: {exc}")
        lock_fp.close()
