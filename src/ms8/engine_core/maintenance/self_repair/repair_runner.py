from __future__ import annotations

import collections
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ...file_write_guard import atomic_write_json
from .repair_audit import append_repair_audit, save_repair_report
from .repair_policies import get_hooks
from .repair_schema import RepairExecutionRow, utc_now_iso
from .repair_validator import verify_repair

SEALED_WHITELIST = {
    "security": {
        "shadow_self_heal",
        "shadow_reset_checkpoint",
        "shadow_replay_spool",
        "fix_shadow_permissions",
    }
}
APPLY_MAX_PER_CHECK_24H = 3
REPAIR_LOCK_STALE_SECONDS = 3600


def _fingerprint(action: str, target: str, params: dict[str, Any]) -> str:
    raw = json.dumps(
        {
            "action": str(action or ""),
            "target": str(target or ""),
            "params": params if isinstance(params, dict) else {},
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()


def _recently_executed(memory_dir: Path, fingerprint: str, action: str, within_seconds: int = 600) -> bool:
    now = time.time()
    # 1) self-repair audit
    audit = memory_dir / "logs" / "repair_ops_audit.jsonl"
    if audit.exists():
        for ln in reversed(audit.read_text(encoding="utf-8", errors="ignore").splitlines()[-2000:]):
            raw = ln.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            ts = str(row.get("timestamp", "") or "")
            fp = str(row.get("action_fingerprint", "") or "")
            act = str(row.get("action", "") or "")
            if fp and fp != fingerprint:
                continue
            if not fp and act != action:
                continue
            try:
                t = ts.replace("Z", "+00:00")
                age = now - __import__("datetime").datetime.fromisoformat(t).timestamp()
                if age <= max(1, within_seconds):
                    return True
            except ValueError:
                continue
    # 2) maintenance policy log fallback by action
    plog = memory_dir / "maintenance_policy_log.jsonl"
    if plog.exists():
        for ln in reversed(plog.read_text(encoding="utf-8", errors="ignore").splitlines()[-2000:]):
            raw = ln.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if str(row.get("action", "") or "") != action:
                continue
            ts = str(row.get("timestamp", "") or "")
            try:
                t = ts.replace("Z", "+00:00")
                age = now - __import__("datetime").datetime.fromisoformat(t).timestamp()
                if age <= max(1, within_seconds):
                    return True
            except ValueError:
                continue
    return False


def _parse_iso(ts: str) -> datetime | None:
    raw = str(ts or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _count_recent_check_attempts(memory_dir: Path, check_id: str, hours: int = 24) -> int:
    audit = memory_dir / "logs" / "repair_ops_audit.jsonl"
    if not audit.exists():
        return 0
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=max(1, int(hours)))
    count = 0
    for ln in reversed(audit.read_text(encoding="utf-8", errors="ignore").splitlines()[-5000:]):
        raw = ln.strip()
        if not raw:
            continue
        try:
            row = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if str(row.get("check_id", "") or "") != str(check_id or ""):
            continue
        if str(row.get("mode", "") or "") != "apply":
            continue
        ts = _parse_iso(str(row.get("timestamp", "") or ""))
        if ts is None or ts < cutoff:
            continue
        count += 1
    return count


def _acquire_repair_lock(memory_dir: Path) -> tuple[bool, str]:
    state_dir = memory_dir / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    lock = state_dir / "repair_in_progress.lock"
    now = time.time()
    payload = {"pid": os.getpid(), "started_at": utc_now_iso()}
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False))
        return True, str(lock)
    except FileExistsError:
        try:
            age = now - float(lock.stat().st_mtime)
        except OSError:
            age = 0.0
        if age > float(REPAIR_LOCK_STALE_SECONDS):
            try:
                lock.unlink(missing_ok=True)
            except OSError as exc:
                print(f"[SelfRepairRunner] Failed removing stale repair lock {lock}: {exc}")
            try:
                fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(json.dumps(payload, ensure_ascii=False))
                return True, str(lock)
            except OSError:
                return False, str(lock)
        return False, str(lock)
    except OSError:
        return False, str(lock)


def _release_repair_lock(lock_path: str) -> None:
    try:
        Path(lock_path).unlink(missing_ok=True)
    except OSError as exc:
        print(f"[SelfRepairRunner] Failed releasing repair lock {lock_path}: {exc}")


def _save_snapshot(memory_dir: Path, operation_id: str, stage: str, payload: dict[str, Any]) -> str:
    p = memory_dir / "state" / "repair_snapshots"
    p.mkdir(parents=True, exist_ok=True)
    fp = p / f"{operation_id}-{stage}.json"
    atomic_write_json(fp, payload, ensure_ascii=False, indent=2)
    return str(fp)


def _file_probe(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "size": 0, "mtime": 0.0, "sha1": ""}
    try:
        raw = path.read_bytes()
    except OSError:
        raw = b""
    try:
        st = path.stat()
        size = int(st.st_size)
        mtime = float(st.st_mtime)
    except OSError:
        size = len(raw)
        mtime = 0.0
    sha1 = hashlib.sha1(raw).hexdigest() if raw else ""
    return {"exists": True, "size": size, "mtime": mtime, "sha1": sha1}


def _before_probe(memory_dir: Path, action: str, target: str, check_id: str) -> dict[str, Any]:
    out: dict[str, Any] = {"action": action, "target": target, "check_id": check_id}
    if action in {"repair_jsonl"}:
        out["file"] = _file_probe(memory_dir / "auto_memory_records.jsonl")
    elif action in {"rebuild_index"}:
        out["file"] = _file_probe(memory_dir / "auto_memory_index.json")
    elif action in {"shadow_reset_checkpoint"}:
        out["file"] = _file_probe(memory_dir / "shadow_data" / "shadow_checkpoints.jsonl")
    elif action in {"shadow_replay_spool"}:
        out["spool_pending_file"] = _file_probe(memory_dir / "shadow_data" / "seal_manifest.json")
    return out


def _rollback_verify(memory_dir: Path, before_probe: dict[str, Any], action: str) -> dict[str, Any]:
    # Best-effort verification: compare primary target probe with pre-apply snapshot.
    want = before_probe.get("file", {}) if isinstance(before_probe.get("file", {}), dict) else {}
    if not want:
        return {"status": "skipped", "reason": "no_before_probe"}
    path: Path | None = None
    if action == "repair_jsonl":
        path = memory_dir / "auto_memory_records.jsonl"
    elif action == "rebuild_index":
        path = memory_dir / "auto_memory_index.json"
    elif action == "shadow_reset_checkpoint":
        path = memory_dir / "shadow_data" / "shadow_checkpoints.jsonl"
    if path is None:
        return {"status": "skipped", "reason": "unsupported_action"}
    got = _file_probe(path)
    ok = (
        bool(got.get("exists", False)) == bool(want.get("exists", False))
        and str(got.get("sha1", "")) == str(want.get("sha1", ""))
        and int(got.get("size", 0)) == int(want.get("size", 0))
    )
    return {"status": "ok" if ok else "mismatch", "before": want, "after": got, "path": str(path)}


def _root_cause_probe(
    core: Any,
    row: dict[str, Any],
    *,
    apply_result: dict[str, Any] | None = None,
    verify_result: dict[str, Any] | None = None,
    error: str = "",
) -> dict[str, Any]:
    """Collect non-blocking root-cause hints for R2/R3 actions."""
    check_id = str(row.get("check_id", "") or "")
    action = str(row.get("action", "") or "")
    out: dict[str, Any] = {
        "check_id": check_id,
        "action": action,
        "error": str(error or "")[:500],
        "hints": [],
    }
    try:
        if action in {"restart_launchd_mcp", "restart_launchd_maintenance"}:
            label = "com.openclaw.memory.mcp" if action.endswith("_mcp") else "com.openclaw.memory.maintenance"
            cp = subprocess.run(["launchctl", "list", label], capture_output=True, text=True, timeout=3, check=False)
            out["launchd"] = {
                "label": label,
                "returncode": int(cp.returncode),
                "stderr": str(cp.stderr or "")[-400:],
            }
            if cp.returncode != 0:
                out["hints"].append("launchd_service_not_running")
        if check_id == "l1_disk_space" or action == "cleanup_disk":
            du = shutil.disk_usage(str(Path(core.config["memory_dir"])))
            free_gb = round(float(du.free) / (1024 * 1024 * 1024), 3)
            out["disk"] = {"free_gb": free_gb}
            if free_gb < 1.0:
                out["hints"].append("disk_critically_low")
        if action.startswith("shadow_") or check_id.startswith("l3_"):
            st = core.shadow_status() if hasattr(core, "shadow_status") else {}
            out["shadow_status"] = st if isinstance(st, dict) else {}
            if isinstance(st, dict) and bool(st.get("sealed", False)):
                out["hints"].append("shadow_still_sealed")
        if apply_result and str(apply_result.get("status", "")).lower() in {"error", "failed"}:
            out["hints"].append("apply_failed")
        if verify_result and str(verify_result.get("status", "")).lower() in {
            "error",
            "fail",
            "failed",
        }:
            out["hints"].append("verify_failed")
    except (OSError, subprocess.SubprocessError, TypeError, ValueError) as exc:
        out["probe_error"] = str(exc)
    return out


def _next_operation_id(base_check_id: str, base_action: str) -> str:
    raw = f"{base_check_id}|{base_action}|{utc_now_iso()}"
    sig = hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:10]
    return f"repair-follow-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{sig}"


def _default_dynamic_chain_rules() -> list[dict[str, Any]]:
    return [
        {
            "enabled": True,
            "match_action": "shadow_reset_checkpoint",
            "match_check_id": "l2_sqlite_integrity",
            "on_apply_status": ["ok", "success"],
            "append": [
                {
                    "check_id": "l2_index_consistency",
                    "action": "rebuild_index",
                    "domain": "memory",
                    "risk": "R2",
                    "reason": "dynamic_followup_after_sqlite_recovery",
                    "target": "memory:index",
                    "params": {"dynamic": True, "from": "l2_sqlite_integrity"},
                    "action_guide": "Rebuild index after sqlite integrity recovery",
                }
            ],
        },
        {
            "enabled": True,
            "match_action": "shadow_reset_checkpoint",
            "on_apply_status": ["ok", "success"],
            "append": [
                {
                    "check_id": "l3_spool_backlog",
                    "action": "shadow_replay_spool",
                    "domain": "security",
                    "risk": "R2",
                    "reason": "dynamic_followup_after_checkpoint_reset",
                    "target": "shadow:spool",
                    "params": {"dynamic": True},
                    "action_guide": "Replay spool after checkpoint reset",
                }
            ],
        },
        {
            "enabled": True,
            "match_action": "repair_jsonl",
            "on_apply_status": ["ok", "success"],
            "append": [
                {
                    "check_id": "l2_index_consistency",
                    "action": "rebuild_index",
                    "domain": "memory",
                    "risk": "R2",
                    "reason": "dynamic_followup_after_jsonl_repair",
                    "target": "memory:index",
                    "params": {"dynamic": True},
                    "action_guide": "Rebuild index after records repair",
                }
            ],
        },
        {
            "enabled": True,
            "match_action": "rebuild_index",
            "on_apply_status": ["ok", "success"],
            "append": [
                {
                    "check_id": "l2_write_then_search",
                    "action": "probe_write_then_search",
                    "domain": "memory",
                    "risk": "R1",
                    "reason": "dynamic_probe_after_rebuild_index",
                    "target": "memory:retrieval_probe",
                    "params": {"dynamic": True},
                    "action_guide": "Run write-then-search probe after index rebuild",
                }
            ],
        },
    ]


def _load_dynamic_chain_rules(cfg_sc: dict[str, Any]) -> list[dict[str, Any]]:
    chain_cfg = (
        cfg_sc.get("dynamic_repair_chain", {}) if isinstance(cfg_sc.get("dynamic_repair_chain", {}), dict) else {}
    )
    if chain_cfg and (not bool(chain_cfg.get("enabled", True))):
        return []
    rules = chain_cfg.get("rules", [])
    if isinstance(rules, list) and len(rules) > 0:
        out = [r for r in rules if isinstance(r, dict)]
        return out
    return _default_dynamic_chain_rules()


def _append_followups(
    rows: list[dict[str, Any]],
    row: dict[str, Any],
    apply_result: dict[str, Any] | None,
    verify_result: dict[str, Any] | None,
    planned_or_executed: set[tuple[str, str]],
    dynamic_rules: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Result-driven dynamic chain: after successful action, enqueue next step."""
    appended: list[dict[str, Any]] = []
    action = str(row.get("action", "") or "")
    status_apply = str((apply_result or {}).get("status", "")).lower()
    if status_apply in {"error", "failed"}:
        return appended

    follow_specs: list[dict[str, Any]] = []
    check_id = str(row.get("check_id", "") or "")
    for rr in dynamic_rules:
        if not bool(rr.get("enabled", True)):
            continue
        match_action = str(rr.get("match_action", "") or "")
        if match_action and match_action != action:
            continue
        match_check = str(rr.get("match_check_id", "") or "")
        if match_check and match_check != check_id:
            continue
        allowed = rr.get("on_apply_status", ["ok", "success"])
        allowed_set = {str(x).lower() for x in allowed if isinstance(x, (str, int, float))}
        if allowed_set and status_apply not in allowed_set:
            continue
        app = rr.get("append", [])
        if not isinstance(app, list):
            continue
        for item in app:
            if isinstance(item, dict):
                follow_specs.append(dict(item))

    for spec in follow_specs:
        key = (str(spec["action"]), str(spec["target"]))
        if key in planned_or_executed:
            continue
        planned_or_executed.add(key)
        r = dict(spec)
        r["operation_id"] = _next_operation_id(str(spec["check_id"]), str(spec["action"]))
        rows.append(r)
        appended.append(r)
    return appended


def _group_domain(plan: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for item in plan:
        out[str(item.get("domain", "memory"))].append(item)
    return out


def _notify_repair_summary(report: dict[str, Any]) -> dict[str, Any]:
    if sys.platform != "darwin":
        return {"status": "skipped", "reason": "non_macos"}
    summary = report.get("summary", {}) if isinstance(report.get("summary", {}), dict) else {}
    failed = int(summary.get("failed", 0) or 0)
    manual = int(summary.get("needs_manual", 0) or 0)
    executed = report.get("executed", []) if isinstance(report.get("executed", []), list) else []
    r3_success = sum(
        1 for x in executed if str(x.get("result", "")) == "success" and str(x.get("risk", "")).upper() == "R3"
    )
    if failed <= 0 and manual <= 0 and r3_success <= 0:
        return {"status": "skipped", "reason": "no_alert"}
    try:
        body = f"failed={failed}, manual={manual}, r3_success={r3_success}"
        script = f'display notification "{body}" with title "OpenClaw Self-Repair"'
        subprocess.run(["osascript", "-e", script], check=False, capture_output=True, timeout=3)
        return {"status": "ok", "failed": failed, "needs_manual": manual, "r3_success": r3_success}
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "status": "error",
            "error": str(exc),
            "failed": failed,
            "needs_manual": manual,
            "r3_success": r3_success,
        }


def _session_activity_age_seconds(memory_dir: Path) -> float | None:
    candidates = [
        memory_dir / "openclaw_session_ingest_state.json",
        memory_dir / "working_memory.jsonl",
    ]
    now = time.time()
    ages: list[float] = []
    for p in candidates:
        try:
            if p.exists():
                ages.append(max(0.0, now - float(p.stat().st_mtime)))
        except OSError:
            continue
    if not ages:
        return None
    return min(ages)


def _read_runtime_health(memory_dir: Path) -> dict[str, Any]:
    """Best-effort runtime health reader used by repair window gating."""
    candidates = [
        memory_dir.parent / "runtime" / "health.json",
        memory_dir / "runtime" / "health.json",
    ]
    for hp in candidates:
        try:
            if not hp.exists():
                continue
            data = json.loads(hp.read_text(encoding="utf-8", errors="ignore") or "{}")
            if isinstance(data, dict):
                return data
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    return {}


def _runtime_mcp_active_connections(memory_dir: Path) -> int | None:
    """
    Resolve active MCP connections from runtime health payload.

    Supported keys (first non-negative integer wins):
    - mcp_server.active_connections
    - mcp_server.active_clients
    - mcp_server.active_sessions
    """
    health = _read_runtime_health(memory_dir)
    mcp = health.get("mcp_server", {}) if isinstance(health.get("mcp_server", {}), dict) else {}
    for key in ("active_connections", "active_clients", "active_sessions"):
        raw = mcp.get(key)
        if not isinstance(raw, (int, float, str)):
            continue
        try:
            val = int(raw)
        except (TypeError, ValueError):
            continue
        if val >= 0:
            return val
    return None


def _repair_window_gate(
    core: Any,
    memory_dir: Path,
    cfg_sc: dict[str, Any],
    plan_payload: dict[str, Any],
    *,
    risk: str,
    mode: str,
) -> dict[str, Any]:
    """
    Gate heavy repairs during active write/session windows.
    Applies to R2/R3 only.
    """
    if str(mode) != "apply":
        return {"blocked": False}
    if str(risk or "").upper() not in {"R2", "R3"}:
        return {"blocked": False}
    win = cfg_sc.get("repair_window", {}) if isinstance(cfg_sc.get("repair_window", {}), dict) else {}
    if not bool(win.get("enabled", True)):
        return {"blocked": False}
    is_auto = bool(plan_payload.get("auto", False))
    enforce_manual = bool(win.get("enforce_manual", False))
    if (not is_auto) and (not enforce_manual):
        return {"blocked": False}
    try:
        recent_write_seconds = max(1, int(win.get("recent_write_seconds", 300)))
    except (TypeError, ValueError):
        recent_write_seconds = 300
    try:
        session_active_seconds = max(1, int(win.get("session_active_seconds", 120)))
    except (TypeError, ValueError):
        session_active_seconds = 120
    try:
        max_active_connections = max(0, int(win.get("mcp_active_connection_max", 0)))
    except (TypeError, ValueError):
        max_active_connections = 0

    now = datetime.now(timezone.utc)
    last_write_iso = str(getattr(core, "_last_write_success_at", "") or "")
    last_write_age: float | None = None
    if last_write_iso:
        dt = _parse_iso(last_write_iso)
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
            last_write_age = max(0.0, (now - dt).total_seconds())
    session_age = _session_activity_age_seconds(memory_dir)
    active_connections = _runtime_mcp_active_connections(memory_dir)
    reasons: list[str] = []
    details: dict[str, Any] = {
        "auto": is_auto,
        "risk": str(risk),
        "recent_write_seconds": recent_write_seconds,
        "session_active_seconds": session_active_seconds,
        "mcp_active_connection_max": max_active_connections,
        "last_write_age_seconds": last_write_age,
        "session_activity_age_seconds": session_age,
        "mcp_active_connections": active_connections,
    }
    if last_write_age is not None and last_write_age < float(recent_write_seconds):
        reasons.append("recent_write_active")
    if session_age is not None and session_age < float(session_active_seconds):
        reasons.append("session_activity_active")
    if active_connections is not None and active_connections > int(max_active_connections):
        reasons.append("mcp_connections_active")
    if reasons:
        details["reasons"] = reasons
        return {"blocked": True, "error": "repair_window_busy", "details": details}
    return {"blocked": False, "details": details}


def run_repair_plan(core: Any, plan_payload: dict[str, Any], *, mode: str = "dry-run") -> dict[str, Any]:
    memory_dir = Path(core.config["memory_dir"])
    cfg_sc = core.config.get("settings", {}).get("memory", {}).get("self_check", {})
    try:
        apply_max_per_check_24h = max(1, int(cfg_sc.get("self_repair_max_per_check_24h", APPLY_MAX_PER_CHECK_24H)))
    except (TypeError, ValueError):
        apply_max_per_check_24h = APPLY_MAX_PER_CHECK_24H
    dynamic_rules = _load_dynamic_chain_rules(cfg_sc)
    lock_path = ""
    got_lock, lock_path = _acquire_repair_lock(memory_dir)
    if not got_lock:
        return {
            "status": "blocked",
            "mode": mode,
            "error": "repair_in_progress",
            "lock_path": lock_path,
            "started_at": utc_now_iso(),
            "finished_at": utc_now_iso(),
            "summary": {
                "planned": 0,
                "executed": 0,
                "success": 0,
                "failed": 1,
                "rolled_back": 0,
                "needs_manual": 1,
            },
            "executed": [],
        }
    plan_rows = plan_payload.get("plan", []) if isinstance(plan_payload.get("plan", []), list) else []
    dedup_actions_targets: set[tuple[str, str]] = set()
    for r in plan_rows:
        if isinstance(r, dict):
            dedup_actions_targets.add((str(r.get("action", "")), str(r.get("target", ""))))
    started = utc_now_iso()
    executed: list[dict[str, Any]] = []
    failed_domains = set()

    sealed = False
    r3_approved = bool(plan_payload.get("r3_approved", False))
    try:
        if hasattr(core, "shadow_status"):
            st = core.shadow_status()
            sealed = bool(st.get("sealed", False)) if isinstance(st, dict) else False
    except OSError:
        sealed = False

    try:
        for domain, rows in _group_domain(plan_rows).items():
            if domain in failed_domains:
                continue
            for row in rows:
                t0 = time.perf_counter()
                op = str(row.get("operation_id", ""))
                check_id = str(row.get("check_id", ""))
                action = str(row.get("action", ""))
                risk = str(row.get("risk", "R1"))
                target = str(row.get("target", ""))
                params = row.get("params", {}) if isinstance(row.get("params", {}), dict) else {}
                fp = _fingerprint(action, target, params)
                idem = hashlib.sha1(f"{op}|{fp}".encode("utf-8", errors="ignore")).hexdigest()[:16]

                if mode == "apply" and risk.upper() == "R3" and (not r3_approved):
                    exec_row = RepairExecutionRow(
                        operation_id=op,
                        check_id=check_id,
                        action=action,
                        domain=domain,
                        risk=risk,
                        mode=mode,
                        result="blocked",
                        verify_status="skipped",
                        error="r3_requires_approval",
                        action_fingerprint=fp,
                        idempotency_key=idem,
                        duration_ms=int((time.perf_counter() - t0) * 1000),
                    )
                    append_repair_audit(memory_dir, exec_row)
                    executed.append(exec_row.to_dict())
                    continue

                gate = _repair_window_gate(
                    core,
                    memory_dir,
                    cfg_sc,
                    plan_payload,
                    risk=risk,
                    mode=mode,
                )
                if bool(gate.get("blocked", False)):
                    exec_row = RepairExecutionRow(
                        operation_id=op,
                        check_id=check_id,
                        action=action,
                        domain=domain,
                        risk=risk,
                        mode=mode,
                        result="blocked",
                        verify_status="skipped",
                        error=str(gate.get("error", "repair_window_busy")),
                        action_fingerprint=fp,
                        idempotency_key=idem,
                        duration_ms=int((time.perf_counter() - t0) * 1000),
                        details=gate.get("details", {}),
                    )
                    append_repair_audit(memory_dir, exec_row)
                    executed.append(exec_row.to_dict())
                    continue

                if sealed:
                    allowed = action in SEALED_WHITELIST.get(domain, set())
                    if not allowed:
                        exec_row = RepairExecutionRow(
                            operation_id=op,
                            check_id=check_id,
                            action=action,
                            domain=domain,
                            risk=risk,
                            mode=mode,
                            result="blocked",
                            verify_status="skipped",
                            error="blocked_by_shadow_sealed",
                            action_fingerprint=fp,
                            idempotency_key=idem,
                            duration_ms=int((time.perf_counter() - t0) * 1000),
                        )
                        append_repair_audit(memory_dir, exec_row)
                        executed.append(exec_row.to_dict())
                        continue

                if _recently_executed(memory_dir, fp, action, within_seconds=600):
                    exec_row = RepairExecutionRow(
                        operation_id=op,
                        check_id=check_id,
                        action=action,
                        domain=domain,
                        risk=risk,
                        mode=mode,
                        result="skipped",
                        verify_status="skipped",
                        error="dedup_recent_action",
                        action_fingerprint=fp,
                        idempotency_key=idem,
                        duration_ms=int((time.perf_counter() - t0) * 1000),
                    )
                    append_repair_audit(memory_dir, exec_row)
                    executed.append(exec_row.to_dict())
                    continue

                if mode == "apply":
                    attempts = _count_recent_check_attempts(memory_dir, check_id, hours=24)
                    if attempts >= apply_max_per_check_24h:
                        exec_row = RepairExecutionRow(
                            operation_id=op,
                            check_id=check_id,
                            action=action,
                            domain=domain,
                            risk=risk,
                            mode=mode,
                            result="blocked",
                            verify_status="skipped",
                            error="rate_limited_24h",
                            action_fingerprint=fp,
                            idempotency_key=idem,
                            duration_ms=int((time.perf_counter() - t0) * 1000),
                            details={"attempts_24h": attempts, "limit": apply_max_per_check_24h},
                        )
                        append_repair_audit(memory_dir, exec_row)
                        executed.append(exec_row.to_dict())
                        continue

                hooks = get_hooks(action)
                if hooks is None:
                    exec_row = RepairExecutionRow(
                        operation_id=op,
                        check_id=check_id,
                        action=action,
                        domain=domain,
                        risk=risk,
                        mode=mode,
                        result="skipped",
                        verify_status="skipped",
                        error="missing_policy_hooks",
                        action_fingerprint=fp,
                        idempotency_key=idem,
                        duration_ms=int((time.perf_counter() - t0) * 1000),
                    )
                    append_repair_audit(memory_dir, exec_row)
                    executed.append(exec_row.to_dict())
                    continue

                pre = hooks.pre_check(core, row)
                if str(pre.get("status", "ok")) not in {"ok", "success", "skipped"}:
                    exec_row = RepairExecutionRow(
                        operation_id=op,
                        check_id=check_id,
                        action=action,
                        domain=domain,
                        risk=risk,
                        mode=mode,
                        result="blocked",
                        verify_status="skipped",
                        error=f"pre_check:{pre}",
                        action_fingerprint=fp,
                        idempotency_key=idem,
                        duration_ms=int((time.perf_counter() - t0) * 1000),
                        details={"pre_check": pre},
                    )
                    append_repair_audit(memory_dir, exec_row)
                    executed.append(exec_row.to_dict())
                    failed_domains.add(domain)
                    break

                if mode == "dry-run":
                    dry = hooks.dry_run(core, row)
                    probe = _root_cause_probe(core, row, apply_result=dry)
                    exec_row = RepairExecutionRow(
                        operation_id=op,
                        check_id=check_id,
                        action=action,
                        domain=domain,
                        risk=risk,
                        mode=mode,
                        result="dry_run",
                        verify_status="skipped",
                        action_fingerprint=fp,
                        idempotency_key=idem,
                        duration_ms=int((time.perf_counter() - t0) * 1000),
                        details={"pre_check": pre, "dry_run": dry, "root_cause_probe": probe},
                    )
                    append_repair_audit(memory_dir, exec_row)
                    executed.append(exec_row.to_dict())
                    continue

                before = {
                    "shadow_sealed": sealed,
                    "check_id": check_id,
                    "action": action,
                    "target": target,
                    "probe": _before_probe(memory_dir, action, target, check_id),
                    "ts": utc_now_iso(),
                }
                before_path = _save_snapshot(memory_dir, op, "before", before)
                rolled_back = False
                try:
                    applied = hooks.apply(core, row)
                    verify = verify_repair(core, check_id)
                    after = {"apply": applied, "verify": verify, "ts": utc_now_iso()}
                    after_path = _save_snapshot(memory_dir, op, "after", after)
                    ok = bool(verify.get("ok", False))
                    result = "success" if ok else "failed_verify"
                    verify_status = str(verify.get("status", "error"))
                    err = ""
                    allow_chain_on_verify_fail = False
                    if not ok:
                        rb = hooks.rollback(core, row)
                        rolled_back = True
                        before_probe = before.get("probe", {})
                        before_probe_dict = before_probe if isinstance(before_probe, dict) else {}
                        rb_verify = _rollback_verify(memory_dir, before_probe_dict, action)
                        err = f"verify_failed rollback={rb} rollback_verify={rb_verify}"
                    probe = _root_cause_probe(core, row, apply_result=applied, verify_result=verify, error=err)
                    followups = _append_followups(rows, row, applied, verify, dedup_actions_targets, dynamic_rules)
                    if (not ok) and str(check_id) == "l2_sqlite_integrity" and len(followups) > 0:
                        allow_chain_on_verify_fail = True
                    if (not ok) and (not allow_chain_on_verify_fail):
                        failed_domains.add(domain)
                    exec_row = RepairExecutionRow(
                        operation_id=op,
                        check_id=check_id,
                        action=action,
                        domain=domain,
                        risk=risk,
                        mode=mode,
                        result=result,
                        verify_status=verify_status,
                        error=err,
                        rolled_back=rolled_back,
                        action_fingerprint=fp,
                        idempotency_key=idem,
                        duration_ms=int((time.perf_counter() - t0) * 1000),
                        details={
                            "pre_check": pre,
                            "apply": applied,
                            "verify": verify,
                            "root_cause_probe": probe,
                            "dynamic_followups": followups,
                            "before_snapshot": before_path,
                            "after_snapshot": after_path,
                        },
                    )
                    append_repair_audit(memory_dir, exec_row)
                    executed.append(exec_row.to_dict())
                    if (not ok) and (not allow_chain_on_verify_fail):
                        break
                except OSError as exc:
                    rb = hooks.rollback(core, row)
                    rolled_back = True
                    before_probe = before.get("probe", {})
                    before_probe_dict = before_probe if isinstance(before_probe, dict) else {}
                    rb_verify = _rollback_verify(memory_dir, before_probe_dict, action)
                    probe = _root_cause_probe(core, row, apply_result=None, verify_result=None, error=str(exc))
                    exec_row = RepairExecutionRow(
                        operation_id=op,
                        check_id=check_id,
                        action=action,
                        domain=domain,
                        risk=risk,
                        mode=mode,
                        result="error",
                        verify_status="error",
                        error=str(exc),
                        rolled_back=rolled_back,
                        action_fingerprint=fp,
                        idempotency_key=idem,
                        duration_ms=int((time.perf_counter() - t0) * 1000),
                        details={
                            "pre_check": pre,
                            "rollback": rb,
                            "rollback_verify": rb_verify,
                            "root_cause_probe": probe,
                            "before_snapshot": before_path,
                        },
                    )
                    append_repair_audit(memory_dir, exec_row)
                    executed.append(exec_row.to_dict())
                    failed_domains.add(domain)
                    break

        success = sum(1 for x in executed if str(x.get("result", "")) == "success")
        failed = sum(1 for x in executed if str(x.get("result", "")) in {"error", "failed_verify", "blocked"})
        rolled = sum(1 for x in executed if bool(x.get("rolled_back", False)))
        manual = sum(1 for x in executed if str(x.get("result", "")) in {"failed_verify", "error"})
        report = {
            "status": "ok",
            "mode": mode,
            "started_at": started,
            "finished_at": utc_now_iso(),
            "summary": {
                "planned": len(plan_rows),
                "executed": len(executed),
                "success": success,
                "failed": failed,
                "rolled_back": rolled,
                "needs_manual": manual,
            },
            "executed": executed,
        }
        paths = save_repair_report(memory_dir, report)
        report["paths"] = paths
        report["notify"] = _notify_repair_summary(report)
        return report
    finally:
        _release_repair_lock(lock_path)


def rollback_operation(core: Any, operation_id: str) -> dict[str, Any]:
    memory_dir = Path(core.config["memory_dir"])
    op = str(operation_id or "").strip()
    if not op:
        return {"status": "error", "error": "missing_operation_id"}

    audit = memory_dir / "logs" / "repair_ops_audit.jsonl"
    if not audit.exists():
        return {"status": "error", "error": "audit_missing", "operation_id": op}

    target: dict[str, Any] | None = None
    for ln in reversed(audit.read_text(encoding="utf-8", errors="ignore").splitlines()[-5000:]):
        raw = ln.strip()
        if not raw:
            continue
        try:
            row = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if str(row.get("operation_id", "") or "") == op:
            target = row
            break
    if target is None:
        return {"status": "error", "error": "operation_not_found", "operation_id": op}

    action = str(target.get("action", "") or "")
    check_id = str(target.get("check_id", "") or "")
    hooks = get_hooks(action)
    if hooks is None:
        return {
            "status": "error",
            "error": "missing_policy_hooks",
            "operation_id": op,
            "action": action,
        }

    t0 = time.perf_counter()
    try:
        rb = hooks.rollback(core, target)
        result = str(rb.get("status", "ok") if isinstance(rb, dict) else "ok")
        ok = result in {"ok", "success", "noop", "skipped"}
        exec_row = RepairExecutionRow(
            operation_id=f"{op}-rollback",
            check_id=check_id or "manual_rollback",
            action=f"{action}:rollback",
            domain=str(target.get("domain", "memory") or "memory"),
            risk="R2",
            mode="apply",
            result="success" if ok else "error",
            verify_status="skipped",
            error="" if ok else str(rb),
            rolled_back=True,
            action_fingerprint=_fingerprint(f"{action}:rollback", str(target.get("target", "") or ""), {}),
            idempotency_key=hashlib.sha1(f"{op}:rollback".encode("utf-8", errors="ignore")).hexdigest()[:16],
            duration_ms=int((time.perf_counter() - t0) * 1000),
            details={"source_operation_id": op, "rollback_result": rb},
        )
        append_repair_audit(memory_dir, exec_row)
        return {
            "status": "ok" if ok else "error",
            "operation_id": op,
            "rollback_operation_id": exec_row.operation_id,
            "rollback_result": rb,
        }
    except OSError as exc:
        exec_row = RepairExecutionRow(
            operation_id=f"{op}-rollback",
            check_id=check_id or "manual_rollback",
            action=f"{action}:rollback",
            domain=str(target.get("domain", "memory") or "memory"),
            risk="R2",
            mode="apply",
            result="error",
            verify_status="error",
            error=str(exc),
            rolled_back=False,
            action_fingerprint=_fingerprint(f"{action}:rollback", str(target.get("target", "") or ""), {}),
            idempotency_key=hashlib.sha1(f"{op}:rollback".encode("utf-8", errors="ignore")).hexdigest()[:16],
            duration_ms=int((time.perf_counter() - t0) * 1000),
            details={"source_operation_id": op},
        )
        append_repair_audit(memory_dir, exec_row)
        return {
            "status": "error",
            "operation_id": op,
            "error": str(exc),
        }
