from __future__ import annotations

import json
import hashlib
import importlib.metadata
import os
import shutil
import sqlite3
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


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


def _severity_order(status: str) -> int:
    s = str(status or "").lower()
    if s == "error":
        return 4
    if s == "fail":
        return 3
    if s == "warn":
        return 2
    if s == "pass":
        return 1
    return 0


def _domain_summary(report: Dict[str, Any]) -> Dict[str, Any]:
    buckets: Dict[str, Dict[str, Any]] = {}
    for item in report.get("results", []):
        if not isinstance(item, dict):
            continue
        domain = str(item.get("domain", "memory") or "memory").strip().lower()
        status = str(item.get("status", "error") or "error").strip().lower()
        b = buckets.setdefault(domain, {"total": 0, "pass": 0, "warn": 0, "fail": 0, "error": 0})
        b["total"] += 1
        if status in {"pass", "warn", "fail", "error"}:
            b[status] += 1
        else:
            b["error"] += 1
    for b in buckets.values():
        total = max(1, int(b.get("total", 0)))
        b["pass_rate"] = round(float(b.get("pass", 0)) / total, 4)
    return buckets


def _maturity_gate(report: Dict[str, Any]) -> Dict[str, Any]:
    domains = _domain_summary(report)
    memory = domains.get("memory", {"pass_rate": 0.0, "fail": 0, "error": 0})
    security = domains.get("security", {"pass_rate": 0.0, "fail": 0, "error": 0})
    connect = domains.get("connect", {"pass_rate": 0.0, "fail": 0, "error": 0})

    known_noncritical = set()
    raw_known = report.get("known_noncritical_failures", [])
    if isinstance(raw_known, list):
        for x in raw_known:
            known_noncritical.add(str(x))

    memory_critical_fail = 0
    memory_critical_error = 0
    for item in report.get("results", []):
        if not isinstance(item, dict):
            continue
        if str(item.get("domain", "")) != "memory":
            continue
        cid = str(item.get("check_id", ""))
        if cid in known_noncritical:
            continue
        st = str(item.get("status", ""))
        if st == "fail":
            memory_critical_fail += 1
        elif st == "error":
            memory_critical_error += 1

    gate = {
        "memory_ready": bool(memory_critical_fail == 0 and memory_critical_error == 0),
        "security_ready": bool(
            float(security.get("pass_rate", 0.0)) >= 0.95
            and security.get("fail", 0) == 0
            and security.get("error", 0) == 0
        ),
        "connect_ready": bool(
            float(connect.get("pass_rate", 0.0)) >= 0.95
            and connect.get("fail", 0) == 0
            and connect.get("error", 0) == 0
        ),
    }
    gate["memory_critical_fail"] = int(memory_critical_fail)
    gate["memory_critical_error"] = int(memory_critical_error)
    gate["known_noncritical_failures"] = sorted(known_noncritical)
    gate["overall_ready"] = bool(gate["memory_ready"] and gate["security_ready"] and gate["connect_ready"])
    return gate


def _p95(values: List[float]) -> float:
    if not values:
        return 0.0
    arr = sorted(float(x) for x in values)
    idx = min(len(arr) - 1, int(len(arr) * 0.95))
    return float(arr[idx])


def _detect_performance_regression(history_dir: Path, report: Dict[str, Any], lookback: int = 10) -> Dict[str, Any]:
    history_files = sorted(history_dir.glob("*.json"), reverse=True)[: max(1, int(lookback))]
    baseline: Dict[str, List[float]] = {}
    for p in history_files:
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        for row in payload.get("results", []):
            if not isinstance(row, dict):
                continue
            cid = str(row.get("check_id", ""))
            if not cid:
                continue
            ms = float(row.get("duration_ms", 0.0) or 0.0)
            if ms <= 0:
                continue
            baseline.setdefault(cid, []).append(ms)

    regressions: List[Dict[str, Any]] = []
    for row in report.get("results", []):
        if not isinstance(row, dict):
            continue
        cid = str(row.get("check_id", ""))
        cur = float(row.get("duration_ms", 0.0) or 0.0)
        samples = baseline.get(cid, [])
        if len(samples) < 3 or cur <= 0:
            continue
        p95 = _p95(samples)
        if p95 > 0 and cur > (2.0 * p95):
            row["performance_regression"] = True
            row["performance_baseline_p95_ms"] = round(p95, 2)
            regressions.append(
                {
                    "check_id": cid,
                    "current_ms": round(cur, 2),
                    "baseline_p95_ms": round(p95, 2),
                    "samples": len(samples),
                }
            )
    return {"count": len(regressions), "items": regressions}


def _load_repair_summary(memory_dir: Path) -> Dict[str, Any]:
    from ..self_repair.repair_audit import summarize_repair_7d

    latest = memory_dir / "reports" / "repair_latest.json"
    summary_7d = summarize_repair_7d(memory_dir, days=7)
    if not latest.exists():
        return {"status": "missing", "window_7d": summary_7d}
    try:
        payload = json.loads(latest.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"status": "error", "error": str(exc), "window_7d": summary_7d}
    summary = payload.get("summary", {}) if isinstance(payload.get("summary", {}), dict) else {}
    return {
        "status": "ok",
        "last_repair_at": payload.get("finished_at", payload.get("started_at", "")),
        "mode": payload.get("mode", ""),
        "planned": int(summary.get("planned", 0) or 0),
        "executed": int(summary.get("executed", 0) or 0),
        "success": int(summary.get("success", 0) or 0),
        "failed": int(summary.get("failed", 0) or 0),
        "rolled_back": int(summary.get("rolled_back", 0) or 0),
        "needs_manual": int(summary.get("needs_manual", 0) or 0),
        "window_7d": summary_7d,
    }


def render_markdown(report: Dict[str, Any]) -> str:
    summary = report.get("summary", {}) if isinstance(report.get("summary", {}), dict) else {}
    domains = report.get("domain_summary", {}) if isinstance(report.get("domain_summary", {}), dict) else {}
    gate = report.get("maturity_gate", {}) if isinstance(report.get("maturity_gate", {}), dict) else {}
    lines = [
        "# Self Check Report",
        f"- started_at: {report.get('started_at','')}",
        f"- finished_at: {report.get('finished_at','')}",
        f"- level: {report.get('requested_level','L1')}",
        f"- status: {report.get('status','unknown')}",
        f"- total: {summary.get('total',0)}",
        f"- pass: {summary.get('pass',0)}",
        f"- warn: {summary.get('warn',0)}",
        f"- fail: {summary.get('fail',0)}",
        f"- error: {summary.get('error',0)}",
        f"- exit_code: {summary.get('exit_code',0)}",
        "",
    ]
    if domains:
        lines.append("## Domain Coverage")
        for name in ("memory", "security", "connect"):
            d = domains.get(name)
            if not isinstance(d, dict):
                continue
            lines.append(
                f"- `{name}` total={d.get('total',0)} pass={d.get('pass',0)} warn={d.get('warn',0)} fail={d.get('fail',0)} error={d.get('error',0)} pass_rate={d.get('pass_rate',0)}"
            )
        lines.append("")
    if gate:
        lines.append("## Maturity Gate")
        lines.append(f"- memory_ready: {gate.get('memory_ready', False)}")
        lines.append(f"- security_ready: {gate.get('security_ready', False)}")
        lines.append(f"- connect_ready: {gate.get('connect_ready', False)}")
        lines.append(f"- overall_ready: {gate.get('overall_ready', False)}")
        lines.append("")
    for item in report.get("results", []):
        if not isinstance(item, dict):
            continue
        icon = "✅"
        st = str(item.get("status", ""))
        if st == "warn":
            icon = "⚠️"
        elif st in {"fail", "error"}:
            icon = "❌"
        lines.append(
            f"- {icon} `{item.get('check_id','')}` [{item.get('level','')}/{item.get('domain','memory')}] "
            f"{st} - {item.get('message','')}"
        )
        if item.get("action_guide"):
            lines.append(f"  - action: {item.get('action_guide')}")
    return "\n".join(lines) + "\n"


def _report_dirs(memory_dir: Path) -> Dict[str, Path]:
    reports = memory_dir / "reports"
    history = reports / "self_check_history"
    daily = reports / "self_check_daily"
    reports.mkdir(parents=True, exist_ok=True)
    history.mkdir(parents=True, exist_ok=True)
    daily.mkdir(parents=True, exist_ok=True)
    return {
        "reports": reports,
        "history": history,
        "daily": daily,
        "latest_json": reports / "self_check_latest.json",
        "latest_md": reports / "self_check_latest.md",
        "alert_state": reports / "self_check_alert_state.json",
        "daily_summary": reports / "self_check_daily_summary.md",
    }


def _cleanup_history(history_dir: Path, keep_days: int = 30, keep_max: int = 500) -> Dict[str, Any]:
    removed = 0
    now = _utc_now()
    files = sorted(history_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)
    for idx, p in enumerate(files):
        age_days = (now - datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)).days
        if idx >= keep_max or age_days > keep_days:
            p.unlink(missing_ok=True)
            removed += 1
    return {"removed": removed, "remaining": len(list(history_dir.glob('*.json')))}


def _update_alert_state(report: Dict[str, Any], alert_state_path: Path, cooldown_hours: int = 6, max_per_day: int = 3) -> Dict[str, Any]:
    state: Dict[str, Any] = {}
    if alert_state_path.exists():
        try:
            state = json.loads(alert_state_path.read_text(encoding="utf-8"))
        except Exception:
            state = {}
    if not isinstance(state, dict):
        state = {}

    now = _utc_now()
    result: Dict[str, Any] = {"emitted": [], "muted": []}
    for row in report.get("results", []):
        if not isinstance(row, dict):
            continue
        status = str(row.get("status", ""))
        if status not in {"warn", "fail", "error"}:
            continue
        cid = str(row.get("check_id", ""))
        if not cid:
            continue
        key = f"{cid}:{status}"
        bucket = state.get(key, {}) if isinstance(state.get(key, {}), dict) else {}
        last = _to_aware(str(bucket.get("last_notified_at", "")))
        day = str(now.date())
        day_count = int(bucket.get("count_today", 0) or 0)
        day_mark = str(bucket.get("day", ""))
        if day_mark != day:
            day_count = 0

        in_cooldown = bool(last and now - last < timedelta(hours=max(1, int(cooldown_hours))))
        if in_cooldown or day_count >= max_per_day:
            result["muted"].append({"check_id": cid, "status": status, "reason": "cooldown_or_daily_limit"})
            continue

        day_count += 1
        state[key] = {
            "last_notified_at": now.isoformat(),
            "count_today": day_count,
            "day": day,
        }
        result["emitted"].append({"check_id": cid, "status": status})

    # reset pass rows
    for row in report.get("results", []):
        if not isinstance(row, dict):
            continue
        if str(row.get("status", "")) != "pass":
            continue
        cid = str(row.get("check_id", ""))
        for st in ("warn", "fail", "error"):
            state.pop(f"{cid}:{st}", None)

    _atomic_write_text(alert_state_path, json.dumps(state, ensure_ascii=False, indent=2))
    return result


def _emit_macos_notifications(alert_emit: Dict[str, Any]) -> Dict[str, Any]:
    emitted = alert_emit.get("emitted", []) if isinstance(alert_emit.get("emitted", []), list) else []
    if not emitted:
        return {"status": "skipped", "reason": "no_emitted"}
    if sys.platform != "darwin":
        return {"status": "skipped", "reason": "non_macos"}
    try:
        rows = [f"{str(x.get('check_id',''))}:{str(x.get('status',''))}" for x in emitted[:3] if isinstance(x, dict)]
        body = " ; ".join([r for r in rows if r]) or "self-check alerts emitted"
        script = f'display notification "{body}" with title "OpenClaw Self-Check"'
        subprocess.run(["osascript", "-e", script], check=False, capture_output=True, timeout=3)
        return {"status": "ok", "count": len(emitted)}
    except Exception as exc:
        return {"status": "error", "error": str(exc), "count": len(emitted)}


def persist_report(memory_dir: Path, report: Dict[str, Any], keep_days: int = 30, keep_max: int = 500, cooldown_hours: int = 6, max_alerts_per_day: int = 3) -> Dict[str, Any]:
    dirs = _report_dirs(memory_dir)
    latest_json = dirs["latest_json"]
    latest_md = dirs["latest_md"]

    if "domain_summary" not in report or not isinstance(report.get("domain_summary"), dict):
        report["domain_summary"] = _domain_summary(report)
    if "maturity_gate" not in report or not isinstance(report.get("maturity_gate"), dict):
        report["maturity_gate"] = _maturity_gate(report)
    if "repair_summary" not in report or not isinstance(report.get("repair_summary"), dict):
        report["repair_summary"] = _load_repair_summary(memory_dir)
    report["performance_regression"] = _detect_performance_regression(dirs["history"], report)

    _atomic_write_text(latest_json, json.dumps(report, ensure_ascii=False, indent=2))
    _atomic_write_text(latest_md, render_markdown(report))

    ts = _utc_now().strftime("%Y-%m-%d-%H%M%S")
    hist = dirs["history"] / f"{ts}.json"
    _atomic_write_text(hist, json.dumps(report, ensure_ascii=False, indent=2))

    cleanup = _cleanup_history(dirs["history"], keep_days=keep_days, keep_max=keep_max)
    alert_emit = _update_alert_state(
        report,
        dirs["alert_state"],
        cooldown_hours=cooldown_hours,
        max_per_day=max_alerts_per_day,
    )
    notify_emit = _emit_macos_notifications(alert_emit)

    _write_daily_summary(memory_dir)

    return {
        "latest_json": str(latest_json),
        "latest_md": str(latest_md),
        "history_file": str(hist),
        "cleanup": cleanup,
        "alert_emit": alert_emit,
        "notify_emit": notify_emit,
    }


def load_latest(memory_dir: Path) -> Dict[str, Any]:
    latest = _report_dirs(memory_dir)["latest_json"]
    if not latest.exists():
        return {"status": "missing", "path": str(latest)}
    try:
        return json.loads(latest.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"status": "error", "error": str(exc), "path": str(latest)}


def list_history(memory_dir: Path, limit: int = 10, level: str = "") -> List[Dict[str, Any]]:
    history_dir = _report_dirs(memory_dir)["history"]
    rows: List[Dict[str, Any]] = []
    target_level = str(level or "").upper().strip()
    for p in sorted(history_dir.glob("*.json"), reverse=True):
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        req_level = str(payload.get("requested_level", "")).upper()
        if target_level and req_level != target_level:
            continue
        summary = payload.get("summary", {}) if isinstance(payload.get("summary", {}), dict) else {}
        rows.append(
            {
                "path": str(p),
                "timestamp": str(payload.get("finished_at", payload.get("started_at", ""))),
                "level": req_level,
                "status": str(payload.get("status", "unknown")),
                "pass": int(summary.get("pass", 0) or 0),
                "warn": int(summary.get("warn", 0) or 0),
                "fail": int(summary.get("fail", 0) or 0),
                "error": int(summary.get("error", 0) or 0),
                "exit_code": int(summary.get("exit_code", 2) or 2),
            }
        )
        if len(rows) >= max(1, int(limit)):
            break
    return rows


def _write_daily_summary(memory_dir: Path) -> None:
    dirs = _report_dirs(memory_dir)
    latest = load_latest(memory_dir)
    if not isinstance(latest, dict) or latest.get("status") in {"missing", "error"}:
        return
    rows = list_history(memory_dir, limit=50)
    today = str(_utc_now().date())
    today_rows = [r for r in rows if str(r.get("timestamp", "")).startswith(today)]

    worst = "pass"
    for r in today_rows:
        st = str(r.get("status", "pass"))
        if _severity_order(st) > _severity_order(worst):
            worst = st

    lines = [
        "# Self Check Daily Summary",
        f"- date: {today}",
        f"- runs_today: {len(today_rows)}",
        f"- worst_status_today: {worst}",
    ]
    if today_rows:
        last = today_rows[0]
        lines.extend(
            [
                f"- last_run_at: {last.get('timestamp','')}",
                f"- last_level: {last.get('level','')}",
                f"- last_exit_code: {last.get('exit_code',2)}",
            ]
        )
    _atomic_write_text(dirs["daily_summary"], "\n".join(lines) + "\n")


def build_health_card(core: Any, snapshot_reason: str = "schedule") -> Dict[str, Any]:
    snapshot = core.monitoring.status()
    now = _utc_now().isoformat()
    rates = snapshot.get("rates", {}) if isinstance(snapshot.get("rates", {}), dict) else {}
    slo = snapshot.get("slo", {}) if isinstance(snapshot.get("slo", {}), dict) else {}
    self_check = snapshot.get("self_check_stats", {}) if isinstance(snapshot.get("self_check_stats", {}), dict) else {}
    cfg = core.config if isinstance(getattr(core, "config", {}), dict) else {}
    workspace_dir = Path(str(cfg.get("workspace_dir", ""))).expanduser()
    memory_dir = Path(str(cfg.get("memory_dir", ""))).expanduser()
    settings = cfg.get("settings", {}) if isinstance(cfg.get("settings", {}), dict) else {}
    mem_settings = settings.get("memory", {}) if isinstance(settings.get("memory", {}), dict) else {}
    self_check_settings = mem_settings.get("self_check", {}) if isinstance(mem_settings.get("self_check", {}), dict) else {}
    connect_settings = mem_settings.get("connect", {}) if isinstance(mem_settings.get("connect", {}), dict) else {}
    connect_root = Path(str(connect_settings.get("root", os.environ.get("OPENCLAW_MEMORY_AUTO_ROOT", "~/openclaw-memory-auto")))).expanduser()
    hash_min_mb = float(self_check_settings.get("health_card_hash_min_mb", 1.0) or 1.0)
    hash_default_mb = float(self_check_settings.get("health_card_hash_max_mb", 10.0) or 10.0)
    hash_db_mb = float(self_check_settings.get("health_card_hash_max_mb_db", hash_default_mb) or hash_default_mb)
    hash_markdown_mb = float(
        self_check_settings.get("health_card_hash_max_mb_markdown", hash_default_mb) or hash_default_mb
    )
    hash_json_mb = float(self_check_settings.get("health_card_hash_max_mb_json", hash_default_mb) or hash_default_mb)
    # Keep a safe default floor at 1MB, but make it configurable for future tuning.
    floor_mb = max(0.01, hash_min_mb)
    hash_threshold_bytes = {
        "default": int(max(floor_mb, hash_default_mb) * 1024 * 1024),
        "db": int(max(floor_mb, hash_db_mb) * 1024 * 1024),
        "markdown": int(max(floor_mb, hash_markdown_mb) * 1024 * 1024),
        "json": int(max(floor_mb, hash_json_mb) * 1024 * 1024),
    }

    def _sha256(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()

    def _file_info(path: Path, include_hash: bool = True, hash_scope: str = "default") -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "exists": False,
            "size_bytes": 0,
            "mtime": None,
            "integrity_mode": "none",
        }
        try:
            if not path.exists():
                return out
            st = path.stat()
            out["exists"] = True
            out["size_bytes"] = int(st.st_size)
            out["mtime"] = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
            if include_hash:
                threshold = int(hash_threshold_bytes.get(hash_scope, hash_threshold_bytes["default"]))
                if int(st.st_size) <= threshold:
                    out["sha256"] = _sha256(path)
                    out["integrity_mode"] = "sha256"
                else:
                    out["integrity_mode"] = "size_mtime"
                    out["integrity_token"] = f"{int(st.st_size)}:{int(st.st_mtime)}"
        except Exception as exc:
            out["error"] = str(exc)
        return out

    def _jsonl_lines(path: Path) -> int:
        if not path.exists():
            return 0
        count = 0
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            for _ in f:
                count += 1
        return count

    def _sqlite_count(db_path: Path, table: str) -> int:
        allowed_tables = {"entities", "relations", "memory_anchors"}
        if table not in allowed_tables:
            return 0
        if not db_path.exists():
            return 0
        conn = sqlite3.connect(str(db_path), timeout=3.0)
        try:
            row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            return int(row[0] if row else 0)
        finally:
            conn.close()

    def _launchd_status(label: str) -> str:
        try:
            out = subprocess.run(
                ["launchctl", "list", label],
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
            return "running" if out.returncode == 0 else "stopped"
        except Exception:
            return "unknown"

    def _ollama_stats() -> Dict[str, Any]:
        try:
            req = urllib.request.Request("http://127.0.0.1:11434/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            models = payload.get("models", []) if isinstance(payload, dict) else []
            return {"reachable": True, "model_count": len(models)}
        except Exception:
            return {"reachable": False, "model_count": 0}

    def _resolve_db(name: str) -> Path:
        p = memory_dir / name
        try:
            return p.resolve()
        except Exception:
            return p

    whoosh_root = memory_dir / "index" / "whoosh_index"
    if not whoosh_root.exists():
        whoosh_root = memory_dir / "whoosh_index"

    records_path = memory_dir / "auto_memory_records.jsonl"
    if not records_path.exists():
        records_path = memory_dir / "logs" / "auto_memory_records.jsonl"

    memory_db = _resolve_db("memory.db")
    kg_db = _resolve_db("knowledge_graph.db")
    ollama = _ollama_stats()
    disk = shutil.disk_usage(workspace_dir if workspace_dir.exists() else memory_dir)
    try:
        venv_package_count = len(list(importlib.metadata.distributions()))
    except Exception:
        venv_package_count = None

    files = {
        "MEMORY_md": _file_info(workspace_dir / "MEMORY.md", include_hash=True, hash_scope="markdown"),
        "memory_db": _file_info(memory_db, include_hash=True, hash_scope="db"),
        "knowledge_graph_db": _file_info(kg_db, include_hash=True, hash_scope="db"),
        "config_yaml": _file_info(workspace_dir / "config.yaml", include_hash=True, hash_scope="json"),
        "auto_memory_records_jsonl": _file_info(records_path, include_hash=False, hash_scope="json"),
        "seal_manifest_json": _file_info(
            memory_dir / "security" / "shadow_data" / "seal_manifest.json", include_hash=False, hash_scope="json"
        ),
    }

    counts = {
        "auto_memory_entries": _jsonl_lines(records_path),
        "kg_entities": _sqlite_count(kg_db, "entities"),
        "kg_relations": _sqlite_count(kg_db, "relations"),
        "kg_memory_anchors": _sqlite_count(kg_db, "memory_anchors"),
        "whoosh_segments": len(list(whoosh_root.glob("*.seg"))) if whoosh_root.exists() else 0,
        "memory_db_entities": _sqlite_count(memory_db, "entities"),
        "memory_db_relations": _sqlite_count(memory_db, "relations"),
    }

    services = {
        "launchd_mcp": _launchd_status("com.openclaw.memory.mcp"),
        "launchd_maintenance": _launchd_status("com.openclaw.memory.maintenance"),
        "ollama_reachable": bool(ollama.get("reachable", False)),
        "ollama_model_count": int(ollama.get("model_count", 0) or 0),
    }

    environment = {
        "disk_free_gb": round(float(disk.free) / float(1024**3), 3),
        "disk_used_percent": int(round(100.0 * float(disk.used) / max(1.0, float(disk.total)))),
        "python_version": sys.version.split()[0],
        "venv_package_count": venv_package_count,
        "config_hash": (files.get("config_yaml", {}) or {}).get("sha256", "")[:16],
    }

    runtime = {
        "slo_all_ok": bool(slo.get("all_ok", False)),
        "capture_rate": rates.get("capture_rate"),
        "injection_rate": rates.get("injection_rate"),
        "duplicate_drop_rate": rates.get("duplicate_drop_rate"),
        "shadow_sealed": bool((snapshot.get("shadow_runtime_stats", {}) or {}).get("sealed", False)),
        "self_check_latest_level": self_check.get("latest_level", ""),
        "self_check_latest_exit_code": self_check.get("latest_exit_code", None),
        "self_check_latest_age_minutes": self_check.get("latest_age_minutes", None),
    }

    card = {
        # compatibility fields (v1 readers)
        "generated_at": now,
        "slo_all_ok": runtime["slo_all_ok"],
        "capture_rate": runtime["capture_rate"],
        "injection_rate": runtime["injection_rate"],
        "duplicate_drop_rate": runtime["duplicate_drop_rate"],
        "shadow_sealed": runtime["shadow_sealed"],
        "self_check_latest_level": runtime["self_check_latest_level"],
        "self_check_latest_exit_code": runtime["self_check_latest_exit_code"],
        "self_check_latest_age_minutes": runtime["self_check_latest_age_minutes"],
        # v2 domains
        "files": files,
        "counts": counts,
        "services": services,
        "environment": environment,
        "runtime": runtime,
        "meta": {
            "snapshot_ts": now,
            "snapshot_reason": str(snapshot_reason or "schedule"),
            "card_version": 2,
            "connect_root": str(connect_root),
        },
    }
    return card


def _diff_health_card(previous: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Any]:
    diffs: List[Dict[str, Any]] = []

    def _add(field: str, baseline: Any, cur: Any, typ: str, severity: str, change_pct: float | None = None) -> None:
        row: Dict[str, Any] = {
            "field": field,
            "baseline": baseline,
            "current": cur,
            "type": typ,
            "severity": severity,
        }
        if change_pct is not None:
            row["change_pct"] = round(float(change_pct), 2)
        diffs.append(row)

    prev_files = previous.get("files", {}) if isinstance(previous.get("files", {}), dict) else {}
    cur_files = current.get("files", {}) if isinstance(current.get("files", {}), dict) else {}
    if not prev_files and cur_files:
        prev_meta = previous.get("meta", {}) if isinstance(previous.get("meta", {}), dict) else {}
        cur_meta = current.get("meta", {}) if isinstance(current.get("meta", {}), dict) else {}
        # legacy v1 health card migration: only report info, avoid noisy warnings
        _add("meta.card_version", prev_meta.get("card_version", 1), cur_meta.get("card_version", 2), "migrated", "info")
    for key in sorted(set(prev_files.keys()) | set(cur_files.keys())):
        a = prev_files.get(key, {}) if isinstance(prev_files.get(key, {}), dict) else {}
        b = cur_files.get(key, {}) if isinstance(cur_files.get(key, {}), dict) else {}
        a_exists = bool(a.get("exists", False))
        b_exists = bool(b.get("exists", False))
        if a_exists != b_exists:
            sev = "critical" if (a_exists and not b_exists) else "warning"
            _add(f"files.{key}.exists", a_exists, b_exists, "missing", sev)
        a_size = int(a.get("size_bytes", 0) or 0)
        b_size = int(b.get("size_bytes", 0) or 0)
        if a_size > 0 and b_size != a_size:
            pct = ((b_size - a_size) / float(a_size)) * 100.0
            if pct < -50.0:
                _add(f"files.{key}.size_bytes", a_size, b_size, "decreased", "critical", pct)
            elif pct < -20.0:
                _add(f"files.{key}.size_bytes", a_size, b_size, "decreased", "warning", pct)
            else:
                _add(f"files.{key}.size_bytes", a_size, b_size, "changed", "info", pct)
        a_sha = str(a.get("sha256", "") or "").strip()
        b_sha = str(b.get("sha256", "") or "").strip()
        if a_sha and b_sha and a_sha != b_sha:
            _add(f"files.{key}.sha256", a_sha[:16], b_sha[:16], "content_changed", "warning")

    prev_counts = previous.get("counts", {}) if isinstance(previous.get("counts", {}), dict) else {}
    cur_counts = current.get("counts", {}) if isinstance(current.get("counts", {}), dict) else {}
    for key in sorted(set(prev_counts.keys()) | set(cur_counts.keys())):
        a = int(prev_counts.get(key, 0) or 0)
        b = int(cur_counts.get(key, 0) or 0)
        if a == b:
            continue
        if a > 0:
            pct = ((b - a) / float(a)) * 100.0
            if pct < -50.0:
                _add(f"counts.{key}", a, b, "decreased", "critical", pct)
            elif pct < -10.0:
                _add(f"counts.{key}", a, b, "decreased", "warning", pct)
            else:
                _add(f"counts.{key}", a, b, "changed", "info", pct)
        else:
            _add(f"counts.{key}", a, b, "changed", "info")

    prev_services = previous.get("services", {}) if isinstance(previous.get("services", {}), dict) else {}
    cur_services = current.get("services", {}) if isinstance(current.get("services", {}), dict) else {}
    for key in sorted(set(prev_services.keys()) | set(cur_services.keys())):
        a = prev_services.get(key)
        b = cur_services.get(key)
        if a != b:
            sev = "warning" if str(a) in {"running", "True", "true"} and str(b) in {"stopped", "False", "false"} else "info"
            _add(f"services.{key}", a, b, "changed", sev)

    prev_env = previous.get("environment", {}) if isinstance(previous.get("environment", {}), dict) else {}
    cur_env = current.get("environment", {}) if isinstance(current.get("environment", {}), dict) else {}
    a_free = float(prev_env.get("disk_free_gb", 0) or 0)
    b_free = float(cur_env.get("disk_free_gb", 0) or 0)
    if b_free < 1.0:
        _add("environment.disk_free_gb", a_free, b_free, "low", "critical")
    elif b_free < 5.0:
        _add("environment.disk_free_gb", a_free, b_free, "low", "warning")

    prev_runtime = previous.get("runtime", previous) if isinstance(previous.get("runtime", previous), dict) else {}
    cur_runtime = current.get("runtime", current) if isinstance(current.get("runtime", current), dict) else {}
    a_sealed = bool(prev_runtime.get("shadow_sealed", False))
    b_sealed = bool(cur_runtime.get("shadow_sealed", False))
    if a_sealed != b_sealed:
        _add("runtime.shadow_sealed", a_sealed, b_sealed, "changed", "warning" if b_sealed else "info")

    summary = {"critical": 0, "warning": 0, "info": 0, "total": len(diffs)}
    for d in diffs:
        sev = str(d.get("severity", "info"))
        if sev not in summary:
            continue
        summary[sev] += 1
    return {"diffs": diffs, "summary": summary}


def persist_health_card(
    memory_dir: Path,
    card: Dict[str, Any],
    keep_max: int = 20,
    write_baseline: bool = False,
    sealed: bool = False,
    force: bool = False,
) -> Dict[str, Any]:
    if sealed and not force:
        return {
            "latest": str(memory_dir / "health_card_latest.json"),
            "baseline": str(memory_dir / "health_card_baseline.json"),
            "baseline_written": False,
            "baseline_forced": bool(write_baseline),
            "history": "",
            "history_count": len(list((memory_dir / "health_card_history").glob("*.json"))) if (memory_dir / "health_card_history").exists() else 0,
            "skipped": True,
            "reason": "shadow_sealed",
        }
    card_file = memory_dir / "health_card_latest.json"
    baseline_file = memory_dir / "health_card_baseline.json"
    baseline_sig_file = memory_dir / "health_card_baseline.sha256"
    hist_dir = memory_dir / "health_card_history"
    hist_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(card_file, json.dumps(card, ensure_ascii=False, indent=2))
    ts = _utc_now().strftime("%Y-%m-%d-%H%M%S")
    hist_file = hist_dir / f"{ts}.json"
    _atomic_write_text(hist_file, json.dumps(card, ensure_ascii=False, indent=2))

    baseline_written = False
    baseline_forced = bool(write_baseline)
    if write_baseline or not baseline_file.exists():
        baseline_payload = json.dumps(card, ensure_ascii=False, indent=2)
        _atomic_write_text(baseline_file, baseline_payload)
        baseline_sig = hashlib.sha256(baseline_payload.encode("utf-8")).hexdigest()
        _atomic_write_text(baseline_sig_file, baseline_sig + "\n")
        baseline_written = True

    files = sorted(hist_dir.glob("*.json"), reverse=True)
    for old in files[keep_max:]:
        old.unlink(missing_ok=True)
    return {
        "latest": str(card_file),
        "baseline": str(baseline_file),
        "baseline_signature": str(baseline_sig_file),
        "baseline_written": baseline_written,
        "baseline_forced": baseline_forced,
        "history": str(hist_file),
        "history_count": len(list(hist_dir.glob("*.json"))),
    }
