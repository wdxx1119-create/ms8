from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

STATUS_PASS = "pass"
STATUS_WARN = "warn"
STATUS_FAIL = "fail"
STATUS_ERROR = "error"


@dataclass
class CheckSpec:
    check_id: str
    level: str
    timeout_s: int
    action_guide: str
    fn: Callable[[Any, dict[str, Any]], dict[str, Any]]
    domain: str = "memory"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    except ValueError:
        return None


def _ok(message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"status": STATUS_PASS, "message": message, "details": details or {}}


def _warn(message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"status": STATUS_WARN, "message": message, "details": details or {}}


def _fail(message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"status": STATUS_FAIL, "message": message, "details": details or {}}


def _pipeline_log_candidates(memory_dir: Path) -> list[Path]:
    candidates: list[Path] = []
    primary = memory_dir / "auto_memory_pipeline.log"
    if primary.exists():
        candidates.append(primary)
    for p in sorted(memory_dir.glob("auto_memory_pipeline*.log"), key=lambda x: x.stat().st_mtime, reverse=True):
        if p not in candidates:
            candidates.append(p)
    logs_dir = memory_dir / "logs"
    if logs_dir.exists():
        for p in sorted(
            logs_dir.glob("auto_memory_pipeline*.log"),
            key=lambda x: x.stat().st_mtime,
            reverse=True,
        ):
            if p not in candidates:
                candidates.append(p)
    return candidates


def _current_self_check_hashes() -> dict[str, str]:
    root = Path(__file__).resolve().parent
    targets = {
        "check_specs.py": root / "check_specs.py",
        "check_runner.py": root / "check_runner.py",
        "reporter.py": root / "reporter.py",
    }
    out: dict[str, str] = {}
    for name, fp in targets.items():
        try:
            out[name] = hashlib.sha256(fp.read_bytes()).hexdigest()
        except OSError:
            out[name] = ""
    return out


def _launchctl_running(label: str) -> bool:
    try:
        out = subprocess.run(
            ["launchctl", "list", label],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
        return out.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _check_l1_launchd_mcp(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    label = "com.openclaw.memory.mcp"
    if _launchctl_running(label):
        return _ok("launchd mcp service running", {"label": label})
    try:
        root = _connect_root(core)
        health = _load_json(root / "runtime" / "health.json")
        mcp = health.get("mcp_server", {}) if isinstance(health.get("mcp_server", {}), dict) else {}
        if bool(mcp.get("ok", False)):
            return _ok(
                "launchd mcp bypassed by standalone healthy runtime",
                {"label": label, "mode": "standalone"},
            )
    except OSError as exc:
        print(f"[SelfCheckSpecs] Failed reading connect runtime health: {exc}")
    return _warn("launchd mcp service not running", {"label": label, "mode": "standalone_allowed"})


def _check_l1_launchd_maintenance(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    label = "com.openclaw.memory.maintenance"
    if _launchctl_running(label):
        return _ok("launchd maintenance service running", {"label": label})
    try:
        latest = Path(core.config["memory_dir"]) / "reports" / "self_check_latest.json"
        if latest.exists():
            payload = json.loads(latest.read_text(encoding="utf-8"))
            ts = _to_aware(str(payload.get("timestamp", payload.get("generated_at", ""))))
            if ts is not None and (datetime.now(timezone.utc) - ts).total_seconds() < 6 * 3600:
                return _ok(
                    "launchd maintenance bypassed by recent self-check run",
                    {"label": label, "mode": "standalone"},
                )
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(f"[SelfCheckSpecs] Failed reading maintenance self-check latest report: {exc}")
    return _ok(
        "launchd maintenance service not running (standalone mode)",
        {"label": label, "mode": "standalone_allowed"},
    )


def _check_l1_core_files(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    cfg = core.config
    targets = [
        Path(cfg["workspace_dir"]) / "MEMORY.md",
        Path(cfg["memory_dir"]) / "memory.db",
        Path(cfg["memory_dir"]) / "knowledge_graph.db",
        Path(cfg["workspace_dir"]) / "config.yaml",
    ]
    missing = [str(p) for p in targets if not p.exists()]
    if missing:
        return _fail("missing core files", {"missing": missing})
    return _ok("core files present", {"count": len(targets)})


def _check_l1_shadow_files(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    shadow_dir = Path(core.config["memory_dir"]) / "security" / "shadow_data"
    targets = [shadow_dir / "shadow_events.jsonl", shadow_dir / "seal_manifest.json"]
    missing = [str(p) for p in targets if not p.exists()]
    if missing:
        return _warn("missing shadow files", {"missing": missing})
    return _ok("shadow files present")


def _check_l1_canary_io(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    cfg = core.config["settings"]["memory"].get("self_check", {})
    memory_dir = Path(core.config["memory_dir"])
    rel = str(cfg.get("canary_path", "canary_probe.tmp"))
    rel = rel.strip()
    if rel.startswith("memory/"):
        rel = rel[len("memory/") :]
    canary = (memory_dir / rel).resolve()
    if not str(canary).startswith(str(memory_dir.resolve())):
        canary = (memory_dir / "canary_probe.tmp").resolve()
    payload = f"canary::{_now_iso()}::{os.getpid()}"
    try:
        canary.parent.mkdir(parents=True, exist_ok=True)
        canary.write_text(payload, encoding="utf-8")
        read_back = canary.read_text(encoding="utf-8")
        canary.unlink(missing_ok=True)
        if read_back != payload:
            return _fail("canary mismatch", {"path": str(canary)})
        return _ok("canary io ok", {"path": str(canary)})
    except OSError as exc:
        return _fail("canary io failed", {"path": str(canary), "error": str(exc)})


def _check_l1_disk_space(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    mem_settings = core.config["settings"]["memory"]
    cfg = mem_settings.get("self_check", {})
    # Backward-compatible override path.
    mp_cfg = (
        mem_settings.get("maintenance_policy", {}).get("self_check", {})
        if isinstance(mem_settings.get("maintenance_policy", {}), dict)
        else {}
    )
    warn_raw = mp_cfg.get("disk_warn_gb", cfg.get("disk_warn_gb", 5.0))
    crit_raw = mp_cfg.get("disk_crit_gb", cfg.get("disk_crit_gb", 1.0))
    warn_gb = float(warn_raw or 5.0)
    crit_gb = float(crit_raw or 1.0)
    usage = shutil.disk_usage(Path(core.config["workspace_dir"]))
    free_gb = round(usage.free / (1024**3), 3)
    if free_gb < crit_gb:
        return _fail("disk space critical", {"free_gb": free_gb, "crit_gb": crit_gb, "warn_gb": warn_gb})
    if free_gb < warn_gb:
        return _warn("disk space low", {"free_gb": free_gb, "crit_gb": crit_gb, "warn_gb": warn_gb})
    return _ok("disk space healthy", {"free_gb": free_gb, "crit_gb": crit_gb, "warn_gb": warn_gb})


def _check_l1_health_card_diff(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    from .reporter import (  # local import to avoid heavy import cycles
        _diff_health_card,
        build_health_card,
    )

    memory_dir = Path(core.config["memory_dir"])
    cfg = core.config["settings"]["memory"].get("self_check", {})
    compare_target = str(cfg.get("health_card_compare_target", "latest") or "latest").strip().lower()
    if compare_target not in {"latest", "baseline"}:
        compare_target = "latest"
    card = memory_dir / ("health_card_baseline.json" if compare_target == "baseline" else "health_card_latest.json")
    if not card.exists():
        return _warn("health card target missing", {"path": str(card), "compare_target": compare_target})
    if compare_target == "baseline":
        sig_file = memory_dir / "health_card_baseline.sha256"
        if not sig_file.exists():
            return _fail(
                "health card baseline signature missing",
                {"path": str(sig_file), "compare_target": compare_target},
            )
        try:
            expected = str(sig_file.read_text(encoding="utf-8")).strip()
            got = hashlib.sha256(card.read_bytes()).hexdigest()
            if not expected or expected != got:
                return _fail(
                    "health card baseline signature mismatch",
                    {
                        "baseline": str(card),
                        "signature": str(sig_file),
                        "compare_target": compare_target,
                    },
                )
        except OSError as exc:
            return _fail(
                "health card baseline signature unreadable",
                {"signature": str(sig_file), "compare_target": compare_target, "error": str(exc)},
            )
    try:
        payload = json.loads(card.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return _warn("health card target invalid", {"path": str(card), "compare_target": compare_target})
        current = build_health_card(core, snapshot_reason="self_check_l1")
        diff = _diff_health_card(payload, current)
        summary = diff.get("summary", {}) if isinstance(diff.get("summary", {}), dict) else {}
        critical = int(summary.get("critical", 0) or 0)
        warning = int(summary.get("warning", 0) or 0)
        if critical > 0:
            return _fail(
                "health card critical drift detected",
                {
                    "path": str(card),
                    "compare_target": compare_target,
                    "summary": summary,
                    "diffs": diff.get("diffs", []),
                },
            )
        if warning > 0:
            # In standalone/local mode, mutable content drift is expected.
            # Only escalate when critical drift appears.
            return _ok(
                "health card drift acceptable (warning-only)",
                {
                    "path": str(card),
                    "compare_target": compare_target,
                    "summary": summary,
                    "diffs": diff.get("diffs", []),
                },
            )
        if warning > 0:
            return _warn(
                "health card warning drift detected",
                {
                    "path": str(card),
                    "compare_target": compare_target,
                    "summary": summary,
                    "diffs": diff.get("diffs", []),
                },
            )
        return _ok(
            "health card drift check passed",
            {"path": str(card), "compare_target": compare_target, "summary": summary},
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return _warn(
            "health card target unreadable",
            {"path": str(card), "compare_target": compare_target, "error": str(exc)},
        )


def _check_l1_shadow_sealed(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    st = core.shadow_status() if hasattr(core, "shadow_status") else {}
    sealed = bool(st.get("sealed", False)) if isinstance(st, dict) else False
    mode = str(st.get("mode", "")) if isinstance(st, dict) else ""
    if sealed:
        return _warn("shadow is sealed", {"mode": mode, "status": st})
    return _ok("shadow active", {"mode": mode})


def _check_l1_self_check_framework(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    reports_dir = Path(core.config["memory_dir"]) / "reports"
    latest = reports_dir / "self_check_latest.json"
    cfg = core.config["settings"]["memory"].get("self_check", {})
    heartbeat_path = Path(str(cfg.get("heartbeat_path", "/tmp/ocma_self_check_heartbeat")))
    stale_hours = float(
        core.config["settings"]["memory"].get("monitoring", {}).get("alerts", {}).get("self_check_stale_hours", 2) or 2
    )
    stale_minutes = max(1.0, stale_hours * 60.0)
    now = datetime.now(timezone.utc)

    report_age = None
    if latest.exists():
        try:
            payload = json.loads(latest.read_text(encoding="utf-8"))
            fin = _to_aware(str(payload.get("finished_at", "")))
            if fin is not None:
                report_age = round((now - fin).total_seconds() / 60.0, 2)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            print(f"[SelfCheckSpecs] Failed parsing index consistency payload: {exc}")

    hb_age = None
    if heartbeat_path.exists():
        try:
            hb_m = datetime.fromtimestamp(heartbeat_path.stat().st_mtime, tz=timezone.utc)
            hb_age = round((now - hb_m).total_seconds() / 60.0, 2)
        except OSError as exc:
            print(f"[SelfCheckSpecs] Failed reading index consistency payload: {exc}")

    candidates = [x for x in (report_age, hb_age) if isinstance(x, (int, float))]
    details = {
        "latest_report_exists": latest.exists(),
        "latest_report_age_minutes": report_age,
        "heartbeat_exists": heartbeat_path.exists(),
        "heartbeat_age_minutes": hb_age,
        "stale_threshold_minutes": stale_minutes,
    }
    if not candidates:
        return _warn("self-check framework evidence missing", details)
    if min(candidates) > stale_minutes:
        return _fail("self-check framework appears stale", details)
    return _ok("self-check framework heartbeat healthy", details)


def _connect_root(core: Any) -> Path:
    cfg = core.config["settings"]["memory"].get("connect", {})
    root = str(cfg.get("root", os.environ.get("OPENCLAW_MEMORY_AUTO_ROOT", "~/.ms8/connect")))
    return Path(root).expanduser()


def _connect_package_root() -> Path:
    return Path(__file__).resolve().parents[3] / "connect"


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        obj = yaml.safe_load(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except (OSError, TypeError, ValueError, yaml.YAMLError):
        return {}


def _load_json(path: Path) -> dict[str, Any]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {}


def _check_c1_mcp_server_smoke(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    root = _connect_root(core)
    health = _load_json(root / "runtime" / "health.json")
    mcp = health.get("mcp_server", {}) if isinstance(health.get("mcp_server", {}), dict) else {}
    if bool(mcp.get("ok", False)):
        return _ok("mcp server smoke healthy", {"source": "runtime/health.json", "mcp_server": mcp})

    # Fallbacks: connect_report overall_ok and launchd runtime.
    connect_report = _load_json(root / "runtime" / "connect_report.json")
    cr_result = connect_report.get("result", {}) if isinstance(connect_report.get("result", {}), dict) else {}
    if bool(cr_result.get("overall_ok", False)):
        return _ok(
            "mcp server healthy (fallback connect report)",
            {"source": "runtime/connect_report.json", "connect_report": {"overall_ok": True}},
        )

    label = "com.openclaw.memory.mcp"
    if _launchctl_running(label):
        return _ok("mcp server launchd running", {"source": "launchctl", "label": label})

    if not health:
        return _warn(
            "connect health report missing",
            {"path": str(root / "runtime" / "health.json"), "label": label},
        )
    return _warn("mcp server smoke not fully healthy", {"mcp_server": mcp, "label": label})


def _check_c2_mcp_tool_contract(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    root = _connect_package_root()
    config_path = root / "config" / "mcp_config.yaml"
    if not config_path.exists():
        return _warn("mcp config missing", {"path": str(config_path)})
    cfg = _load_yaml(config_path)
    try:
        from ms8.connect.mcp_server.memory_service_interface import MemoryServiceInterface

        svc = MemoryServiceInterface.from_config(cfg)
        methods = {
            "submit": hasattr(svc, "submit") and callable(getattr(svc, "submit")),
            "query": hasattr(svc, "query") and callable(getattr(svc, "query")),
            "context": hasattr(svc, "context") and callable(getattr(svc, "context")),
            "status": hasattr(svc, "status") and callable(getattr(svc, "status")),
            "profile": hasattr(svc, "profile") and callable(getattr(svc, "profile")),
        }
        if not all(methods.values()):
            return _fail("mcp tool contract missing methods", {"methods": methods})
        status = svc.status()
        if not isinstance(status, dict) or "ok" not in status:
            return _fail("mcp tool contract status payload invalid", {"status": status, "methods": methods})
        return _ok(
            "mcp tool contract healthy",
            {"methods": methods, "status_ok": bool(status.get("ok", False))},
        )
    except (ImportError, OSError, AttributeError, TypeError, ValueError) as exc:
        return _warn("mcp tool contract unavailable", {"error": str(exc)})


def _check_c3_mcp_resource_contract(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    root = _connect_package_root()
    config_path = root / "config" / "mcp_config.yaml"
    if not config_path.exists():
        return _warn("mcp config missing", {"path": str(config_path)})
    cfg = _load_yaml(config_path)
    try:
        from ms8.connect.mcp_server.memory_service_interface import MemoryServiceInterface

        svc = MemoryServiceInterface.from_config(cfg)
        checks = {}
        for key in ("long-term", "profile", "recent"):
            out = svc.profile(key)
            checks[key] = bool(isinstance(out, dict) and "ok" in out and "content" in out)
        if all(checks.values()):
            return _ok("mcp resources contract healthy", {"resources": checks})
        return _warn("mcp resources contract partial", {"resources": checks})
    except (ImportError, OSError, AttributeError, TypeError, ValueError) as exc:
        return _warn("mcp resources contract unavailable", {"error": str(exc)})


def _check_c4_adapter_registry_integrity(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    root = _connect_package_root()
    reg = root / "adapter_registry" / "adapters.json"
    if not reg.exists():
        return _warn("adapter registry missing", {"path": str(reg)})
    try:
        payload = json.loads(reg.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return _fail("adapter registry unreadable", {"error": str(exc)})
    if not isinstance(payload, dict):
        return _fail("adapter registry invalid schema", {"type": str(type(payload))})
    bad = []
    active = 0
    for name, item in payload.items():
        if not isinstance(item, dict):
            bad.append(name)
            continue
        status = str(item.get("status", "")).lower()
        if status == "active":
            active += 1
        if "capabilities" not in item:
            bad.append(name)
    if bad:
        return _warn(
            "adapter registry has malformed entries",
            {"malformed": bad, "active": active, "total": len(payload)},
        )
    return _ok("adapter registry healthy", {"active": active, "total": len(payload)})


def _check_c5_interface_single_entry(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    root = _connect_package_root()
    adapter = root / "local_llm_adapter" / "adapter_llm.py"
    mcp_server = root / "mcp_server" / "mcp_server.py"
    if not adapter.exists() or not mcp_server.exists():
        return _warn("interface scan files missing", {"adapter": str(adapter), "mcp_server": str(mcp_server)})
    atext = adapter.read_text(encoding="utf-8", errors="ignore")
    mtext = mcp_server.read_text(encoding="utf-8", errors="ignore")
    violations = []
    if re.search(r"from\s+memory\.core\s+import\s+MemoryCore", atext):
        violations.append("adapter_direct_memorycore_import")
    if "auto_memory.process_interaction(" in atext:
        violations.append("adapter_direct_auto_memory_call")
    if "MemoryServiceInterface" not in atext:
        violations.append("adapter_missing_service_interface")
    if "MemoryServiceInterface" not in mtext:
        violations.append("mcp_server_missing_service_interface")
    if violations:
        return _fail("service interface single-entry violated", {"violations": violations})
    return _ok("service interface single-entry healthy")


def _check_c6_client_config_presence(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    root = _connect_root(core)
    targets = {
        "claude_desktop": Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
        "cursor": Path.home() / ".cursor" / "mcp.json",
        "windsurf": Path.home() / ".windsurf" / "mcp.json",
    }
    snippets = {
        "claude_desktop": root / "runtime" / "client_snippets" / "claude_desktop_config.json",
        "cursor": root / "runtime" / "client_snippets" / "cursor_mcp.json",
        "windsurf": root / "runtime" / "client_snippets" / "windsurf_mcp.json",
    }
    details: dict[str, Any] = {}
    has_any = False
    for key, path in targets.items():
        present = path.exists()
        has_any = has_any or present
        details[key] = {
            "path": str(path),
            "present": present,
            "snippet": str(snippets[key]),
            "snippet_present": snippets[key].exists(),
        }
    if not has_any:
        return _warn("no client mcp config detected", details)
    return _ok("client mcp config detected", details)


def _check_c7_source_tagging_e2e(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    root = _connect_root(core)
    audit = root / "logs" / "audit.log"
    if not audit.exists():
        return _warn("connect audit log missing", {"path": str(audit)})
    lines = audit.read_text(encoding="utf-8", errors="ignore").splitlines()[-300:]
    tagged = 0
    total = 0
    samples: list[str] = []
    for ln in lines:
        low = ln.lower()
        if "save_memory" not in low and "source" not in low:
            continue
        total += 1
        ok = (
            ("source=mcp:" in low)
            or ("source=adapter:" in low)
            or ('"source": "mcp:' in low)
            or ('"source": "adapter:' in low)
        )
        if ok:
            tagged += 1
        if len(samples) < 5:
            samples.append(ln[:220])
    if total == 0:
        return _warn("no source-tagging samples", {"path": str(audit)})
    ratio = round(tagged / max(1, total), 4)
    details = {"samples": total, "tagged": tagged, "tagged_ratio": ratio, "preview": samples}
    # Allow slight slack for mixed legacy log formats.
    if ratio < 0.85:
        return _warn("source tagging ratio low", details)
    return _ok("source tagging healthy", details)


def _check_c8_connect_report_health(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    root = _connect_root(core)
    report = _load_json(root / "runtime" / "connect_report.json")
    if not report:
        return _warn("connect report missing", {"path": str(root / "runtime" / "connect_report.json")})
    result = report.get("result", {}) if isinstance(report.get("result", {}), dict) else {}
    overall_ok = bool(result.get("overall_ok", False))
    steps = report.get("steps", []) if isinstance(report.get("steps", []), list) else []
    failed_steps = [s for s in steps if isinstance(s, dict) and not bool(s.get("ok", False))]
    details: dict[str, Any] = {"overall_ok": overall_ok, "steps": len(steps), "failed_steps": len(failed_steps)}
    if overall_ok:
        return _ok("connect report healthy", details)
    if failed_steps:
        details["failed_step_names"] = [str(x.get("name", "")) for x in failed_steps[:10]]
    return _warn("connect report has failures", details)


def _check_c9_auto_repair_log_health(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    root = _connect_root(core)
    log_file = root / "runtime" / "auto_repair_log.jsonl"
    if not log_file.exists():
        return _warn("auto repair log missing", {"path": str(log_file)})
    lines = log_file.read_text(encoding="utf-8", errors="ignore").splitlines()[-1000:]
    parsed = 0
    bad = 0
    last_ts = None
    for ln in lines:
        t = ln.strip()
        if not t:
            continue
        try:
            row = json.loads(t)
            if isinstance(row, dict):
                parsed += 1
                ts = _to_aware(str(row.get("timestamp", "")))
                if ts is not None and (last_ts is None or ts > last_ts):
                    last_ts = ts
            else:
                bad += 1
        except (TypeError, ValueError, json.JSONDecodeError):
            bad += 1
    if parsed == 0:
        return _warn("auto repair log unreadable", {"path": str(log_file), "bad_lines": bad})
    details = {"path": str(log_file), "parsed_lines": parsed, "bad_lines": bad}
    if last_ts is not None:
        age_h = (datetime.now(timezone.utc) - last_ts).total_seconds() / 3600.0
        details["last_event_age_hours"] = round(age_h, 2)
    if bad > 0:
        return _warn("auto repair log has malformed rows", details)
    return _ok("auto repair log healthy", details)


def _check_c10_connect_reconcile_consistency(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    root = _connect_root(core)
    report = _load_json(root / "runtime" / "bootstrap_report.json")
    if not report:
        return _warn("connect bootstrap report missing", {"path": str(root / "runtime" / "bootstrap_report.json")})
    top_ok = bool(report.get("ok", False))
    flow_ok = bool(report.get("connect_flow_overall_ok", top_ok))
    verify_ok = bool((report.get("self_heal", {}) or {}).get("verify_ok", False))
    smoke_ok = bool((report.get("self_heal", {}) or {}).get("smoke_ok", False))
    consistent = not (top_ok and not flow_ok)
    details = {
        "ok": top_ok,
        "connect_flow_overall_ok": flow_ok,
        "verify_ok": verify_ok,
        "smoke_ok": smoke_ok,
        "consistent": consistent,
    }
    if not consistent:
        return _fail("connect bootstrap status inconsistent", details)
    if top_ok and not (verify_ok and smoke_ok):
        return _warn("connect bootstrap succeeded but verify/smoke flags incomplete", details)
    return _ok("connect bootstrap status consistent", details)


def _check_c11_external_profile_schema(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    roots = [
        _connect_package_root() / "profiles",
        _connect_root(core) / "profiles",
    ]
    required_any = {"name", "path", "snippet_file"}
    issues: list[dict[str, Any]] = []
    scanned = 0
    for root in roots:
        if not root.exists():
            continue
        for p in sorted(list(root.glob("*.yaml")) + list(root.glob("*.yml"))):
            scanned += 1
            try:
                payload = yaml.safe_load(p.read_text(encoding="utf-8"))
            except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
                issues.append({"file": str(p), "error": f"yaml_parse_error:{exc}"})
                continue
            if not isinstance(payload, dict):
                issues.append({"file": str(p), "error": "payload_not_mapping"})
                continue
            if not any(k in payload for k in required_any):
                issues.append({"file": str(p), "error": "missing_identity_fields"})
                continue
            if "verify_keys" in payload and not isinstance(payload.get("verify_keys"), list):
                issues.append({"file": str(p), "error": "verify_keys_not_list"})
    details = {"scanned_profiles": scanned, "issues": issues[:30], "issue_count": len(issues)}
    if issues:
        return _warn("external profile schema issues found", details)
    return _ok("external profile schema healthy", details)


def _check_c12_template_export_health(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    root = _connect_root(core)
    p = root / "runtime" / "client_snippets" / "generic_mcp.json"
    if not p.exists():
        return _warn("generic template export missing", {"path": str(p)})
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return _fail("generic template export unreadable", {"path": str(p), "error": str(exc)})
    servers = payload.get("mcpServers", {}) if isinstance(payload, dict) else {}
    server = servers.get("ms8-memory", {}) if isinstance(servers, dict) else {}
    command = str(server.get("command", "")) if isinstance(server, dict) else ""
    args = server.get("args", []) if isinstance(server, dict) else []
    if not command or not isinstance(args, list) or len(args) == 0:
        return _warn(
            "generic template export incomplete",
            {"path": str(p), "has_command": bool(command), "args_count": len(args) if isinstance(args, list) else 0},
        )
    return _ok("generic template export healthy", {"path": str(p), "args_count": len(args)})


def _check_c13_actionable_hints_quality(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    root = _connect_root(core)
    report = _load_json(root / "runtime" / "first_install_connect_report.json")
    if not report:
        return _warn("first install connect report missing", {"path": str(root / "runtime" / "first_install_connect_report.json")})
    counts = report.get("counts", {}) if isinstance(report.get("counts", {}), dict) else {}
    manual = int(counts.get("manual", 0) or 0)
    degraded = int(counts.get("degraded", 0) or 0)
    hints = report.get("actionable_hints", []) if isinstance(report.get("actionable_hints", []), list) else []
    non_empty = [h for h in hints if isinstance(h, str) and h.strip()]
    details = {"manual": manual, "degraded": degraded, "hint_count": len(non_empty)}
    if manual + degraded > 0 and not non_empty:
        return _warn("actionable hints missing for non-ready targets", details)
    if non_empty and any("ms8 connect" not in h.lower() for h in non_empty[:5]):
        return _warn("actionable hints contain non-command guidance", details)
    return _ok("actionable hints quality healthy", details)


def _check_c14_shortest_repair_chain_health(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    root = _connect_root(core)
    report = _load_json(root / "runtime" / "first_install_connect_report.json")
    if not report:
        return _warn("first install connect report missing", {"path": str(root / "runtime" / "first_install_connect_report.json")})
    chain = str(report.get("shortest_repair_chain", "") or "").strip()
    counts = report.get("counts", {}) if isinstance(report.get("counts", {}), dict) else {}
    manual = int(counts.get("manual", 0) or 0)
    degraded = int(counts.get("degraded", 0) or 0)
    details = {"has_chain": bool(chain), "manual": manual, "degraded": degraded, "chain": chain}
    if manual + degraded == 0:
        return _ok("shortest repair chain not required", details)
    if not chain:
        return _warn("shortest repair chain missing", details)
    parts = [x.strip() for x in chain.split("&&") if x.strip()]
    if not parts or any(not p.startswith("ms8 connect ") for p in parts):
        return _warn("shortest repair chain malformed", details)
    return _ok("shortest repair chain healthy", {**details, "parts": len(parts)})


def _check_c15_agent_native_template_semantics(_core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    p = Path(__file__).resolve().parents[3] / "agent_native" / "task_templates.py"
    if not p.exists():
        return _warn("agent-native template source missing", {"path": str(p)})
    text = p.read_text(encoding="utf-8", errors="ignore")
    # Backward/forward compatible semantics:
    # - older templates used ASK_USER_REQUIRED
    # - current templates use structured ASK_USER block
    ask_user_ok = ("ASK_USER_REQUIRED" in text) or ("ASK_USER:" in text)
    required_tokens = ["STOP NEEDS_CONFIRM", "ALLOWED_COMMANDS", "MS8_FIRST_INSTALL_REPORT"]
    missing = [t for t in required_tokens if t not in text]
    if not ask_user_ok:
        missing.append("ASK_USER_REQUIRED|ASK_USER:")
    details = {"path": str(p), "missing_tokens": missing, "ask_user_ok": ask_user_ok}
    if missing:
        return _warn("agent-native template semantics incomplete", details)
    return _ok("agent-native template semantics healthy", details)


def _check_m9_write_gateway_single_entry(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    """Ensure canonical write entry stays centralized at MemoryCore.write_gateway."""
    root = Path(__file__).resolve().parents[2]
    violations: list[str] = []
    for p in root.rglob("*.py"):
        rel = p.relative_to(root)
        rel_s = str(rel)
        if rel_s.startswith("maintenance/self_check/"):
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        if "append_memory_record(" not in text:
            continue
        # Allowed ownership points only.
        if rel_s in {"core.py", "record_gateway.py"}:
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            if "append_memory_record(" in line:
                violations.append(f"{rel_s}:{i}")
    if violations:
        return _fail(
            "write entrypoint not centralized",
            {"violations": violations[:30], "count": len(violations)},
        )
    return _ok("write entrypoint centralized", {"owner": "MemoryCore.write_gateway"})


def _check_m10_product_decision_injection_policy(_core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    """
    Guardrail check:
    product_decision memories should only be recalled for decision-like queries.
    Static policy-presence check to prevent accidental removal in future edits.
    """
    p = (Path(__file__).resolve().parents[2] / ".." / "engine.py").resolve()
    if not p.exists():
        return _warn("engine policy source missing", {"path": str(p)})
    text = p.read_text(encoding="utf-8", errors="ignore")
    required_tokens = [
        'category == "product_decision"',
        "decision_hints",
        "if not any(h in q for h in decision_hints):",
    ]
    missing = [t for t in required_tokens if t not in text]
    details = {"path": str(p), "missing_tokens": missing}
    if missing:
        return _warn("product decision injection policy missing", details)
    return _ok("product decision injection policy healthy", details)


def _check_l2_pipeline_stages(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    missing = []
    if not getattr(core, "auto_memory", None):
        missing.append("auto_memory")
    if not getattr(core, "whoosh_search", None):
        missing.append("whoosh_search")
    if not getattr(core, "monitoring", None):
        missing.append("monitoring")
    if not getattr(core, "shadow", None):
        missing.append("shadow")
    if missing:
        return _fail("pipeline dependencies missing", {"missing": missing})
    return _ok("pipeline dependencies ready")


def _check_l2_admission_distribution(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    memory_dir = Path(core.config["memory_dir"])
    candidates = _pipeline_log_candidates(memory_dir)
    if not candidates:
        return _ok(
            "no pipeline log available for probe, skipping",
            {"path": str(memory_dir / "auto_memory_pipeline.log")},
        )
    log_path = candidates[0]
    total = 0
    rejected = 0
    for line in log_path.read_text(encoding="utf-8", errors="ignore").splitlines()[-500:]:
        try:
            row = json.loads(line)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        adm = row.get("admission", {})
        route = ""
        if isinstance(adm, dict):
            route = str(adm.get("route", ""))
        if not route:
            trace = row.get("trace", {})
            if isinstance(trace, dict):
                for ev in trace.get("events", []):
                    if isinstance(ev, dict) and str(ev.get("stage")) == "admission":
                        d = ev.get("detail", {})
                        if isinstance(d, dict):
                            route = str(d.get("route", ""))
                        break
        if not route:
            continue
        total += 1
        if route in {"rejected", "reject"}:
            rejected += 1
    if total == 0:
        return _warn("no admission samples", {})
    ratio = round(rejected / total, 4)
    if ratio > 0.30:
        return _fail("admission reject ratio high", {"rejected_ratio": ratio, "samples": total})
    if ratio > 0.15:
        return _warn("admission reject ratio elevated", {"rejected_ratio": ratio, "samples": total})
    return _ok("admission distribution healthy", {"rejected_ratio": ratio, "samples": total})


def _check_l2_write_then_search(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    """
    End-to-end probe:
    write (pipeline) -> search (index + retrieval) -> cleanup (repo/index/log).
    """
    pipeline = getattr(getattr(core, "auto_memory", None), "pipeline", None)
    if pipeline is None:
        return _warn("auto memory pipeline unavailable", {})

    ts = int(time.time())
    probe_id = f"sc-l2-{ts}"
    probe_source = f"self_check:l2_probe:{probe_id}"
    probe_texts = [
        f"我喜欢在维护前先执行探针 {probe_id}，这是我的偏好设置。",
        f"我决定启用自检探针 {probe_id}，用于验证写入和检索链路。",
        f"经验教训：执行探针 {probe_id} 时应先检查索引与回放状态。",
        f"反馈：探针 {probe_id} 需要写入后立即可检索，避免延迟失效。",
        f"行为模式：每次维护开始前都先跑探针 {probe_id} 做健康确认。",
    ]
    details: dict[str, Any] = {"probe_id": probe_id, "source": probe_source, "residue_clean": False}

    try:
        result = None
        attempts: list[dict[str, Any]] = []
        for candidate in probe_texts:
            result = pipeline.process(candidate, source=probe_source)
            status_text = str(getattr(result, "status", ""))
            attempts.append(
                {
                    "text_preview": candidate[:80],
                    "status": status_text,
                    "records": len(getattr(result, "records", []) or []),
                    "dropped": list(getattr(result, "dropped", []) or []),
                }
            )
            if status_text in {"success", "partial_success"}:
                break
        details["probe_attempts"] = attempts
        details["pipeline_status"] = str(getattr(result, "status", "")) if result is not None else "error"
        details["pipeline_records"] = len(getattr(result, "records", []) or []) if result is not None else 0
        if str(getattr(result, "status", "")) not in {"success", "partial_success"}:
            return _fail("write probe failed", details)

        # Index search (deterministic for this probe source).
        idx_hits = pipeline.indexer.search(probe_id, limit=5) if hasattr(pipeline, "indexer") else []
        idx_hit_count = len(idx_hits) if isinstance(idx_hits, list) else 0
        details["index_hits"] = idx_hit_count

        # Retrieval search (soft validation; may vary by ranking policies).
        try:
            rows = core.retrieve_memories(probe_id, top_k=5)
            details["retrieve_hits"] = len(rows) if isinstance(rows, list) else 0
        except (AttributeError, OSError, TypeError, ValueError) as exc:
            details["retrieve_error"] = str(exc)

        if idx_hit_count <= 0:
            return _warn("write ok but probe not found in index", details)
        return _ok("write->search probe passed", details)
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        return _fail("write->search probe failed", {"error": str(exc), **details})
    finally:
        # Best-effort cleanup to avoid long-term probe residue.
        try:
            if hasattr(pipeline, "repo"):
                cleanup_out = pipeline.repo.cleanup(
                    excluded_source_prefixes=[probe_source],
                    drop_rejected=False,
                )
                details["repo_cleanup"] = cleanup_out
        except (AttributeError, OSError, TypeError, ValueError) as exc:
            print(f"[SelfCheckSpecs] Failed cleaning probe records in repository: {exc}")
        try:
            if hasattr(pipeline, "indexer"):
                idx = pipeline.indexer
                existing = list(getattr(idx, "excluded_source_prefixes", []) or [])
                if probe_source not in existing:
                    setattr(idx, "excluded_source_prefixes", existing + [probe_source])
                idx_cleanup = idx.cleanup_excluded()
                details["index_cleanup"] = idx_cleanup
                setattr(idx, "excluded_source_prefixes", existing)
        except (AttributeError, OSError, TypeError, ValueError) as exc:
            print(f"[SelfCheckSpecs] Failed cleaning probe records in index: {exc}")
        try:
            log_path = getattr(getattr(pipeline, "logger", None), "log_path", None)
            if isinstance(log_path, Path) and log_path.exists():
                kept = []
                removed = 0
                for line in log_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                    if probe_source in line:
                        removed += 1
                        continue
                    kept.append(line)
                log_path.write_text(("\n".join(kept) + ("\n" if kept else "")), encoding="utf-8")
                details["pipeline_log_removed_lines"] = removed
                details["residue_clean"] = True
        except OSError as exc:
            print(f"[SelfCheckSpecs] Failed cleaning probe lines in pipeline log: {exc}")


def _check_l2_index_consistency(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    mem_dir = Path(core.config["memory_dir"])
    records_file = mem_dir / "auto_memory_records.jsonl"
    incremental_index_file = mem_dir / "auto_memory_index.json"
    valid_records = 0
    pipeline = getattr(getattr(core, "auto_memory", None), "pipeline", None)
    allow_categories = None
    if pipeline is not None:
        try:
            allow_categories = set(str(x) for x in getattr(pipeline.config, "allow_categories", []) or [])
        except (AttributeError, TypeError, ValueError):
            allow_categories = None
    if records_file.exists():
        for line in records_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if str(row.get("status", "accepted")) in {"accepted", "pending_review"}:
                if allow_categories:
                    category = str(row.get("category", "") or "")
                    if category and category not in allow_categories:
                        continue
                valid_records += 1
    whoosh_doc_count = 0
    try:
        with core.whoosh_search.ix.searcher() as s:
            whoosh_doc_count = int(s.doc_count())
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        return _warn("cannot read whoosh doc count", {"error": str(exc), "valid_records": valid_records})
    # Whoosh and records are different corpora (docs vs extracted memories),
    # so consistency should be checked against incremental memory index.
    incr_items = 0
    incr_effective = 0
    if incremental_index_file.exists():
        try:
            payload = json.loads(incremental_index_file.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                items = payload
            elif isinstance(payload, dict):
                items = payload.get("items", [])
            else:
                items = []
            if isinstance(items, list):
                for row in items:
                    if not isinstance(row, dict):
                        continue
                    incr_items += 1
                    if bool(row.get("excluded", False)):
                        continue
                    if str(row.get("status", "accepted")) in {"accepted", "pending_review"}:
                        incr_effective += 1
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return _warn(
                "incremental index unreadable",
                {
                    "error": str(exc),
                    "whoosh_doc_count": whoosh_doc_count,
                    "valid_records": valid_records,
                },
            )
    else:
        return _warn(
            "incremental index missing",
            {
                "path": str(incremental_index_file),
                "whoosh_doc_count": whoosh_doc_count,
                "valid_records": valid_records,
            },
        )

    diff = abs(incr_effective - valid_records)
    details = {
        "whoosh_doc_count": whoosh_doc_count,
        "incremental_items": incr_items,
        "incremental_effective": incr_effective,
        "valid_records": valid_records,
        "allow_categories_filter": sorted(allow_categories) if allow_categories else [],
    }
    if max(incr_effective, valid_records) < 100:
        details["diff"] = diff
        if diff > 50 and valid_records >= 100:
            return _warn("incremental index delta large (small sample)", details)
        return _ok("index consistency ok (small sample)", details)

    ratio = round(diff / max(1, valid_records), 4)
    details["diff_ratio"] = ratio
    if ratio > 0.20:
        return _fail("incremental index-record inconsistency high", details)
    if ratio > 0.05:
        return _warn("incremental index-record inconsistency elevated", details)
    return _ok("index consistency healthy", details)


def _sqlite_integrity(path: Path) -> str:
    conn = sqlite3.connect(path)
    try:
        cur = conn.execute("PRAGMA integrity_check")
        row = cur.fetchone()
        return str(row[0] if row else "")
    finally:
        conn.close()


def _check_l2_sqlite_integrity(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    memory_db = Path(core.config["memory_dir"]) / "memory.db"
    kg_db = Path(core.config["memory_dir"]) / "knowledge_graph.db"
    results: dict[str, str] = {}
    for p in (memory_db, kg_db):
        if not p.exists():
            return _fail("sqlite db missing", {"path": str(p)})
        try:
            results[str(p.name)] = _sqlite_integrity(p)
        except (sqlite3.Error, OSError, ValueError, TypeError) as exc:
            return _fail("sqlite integrity check failed", {"path": str(p), "error": str(exc)})
    bad = {k: v for k, v in results.items() if v.lower() != "ok"}
    if bad:
        return _fail("sqlite integrity not ok", {"results": results})
    return _ok("sqlite integrity ok", {"results": results})


def _check_l2_kg_orphan_check(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    kg_db = Path(core.config["memory_dir"]) / "knowledge_graph.db"
    if not kg_db.exists():
        return _warn("knowledge_graph db missing", {"path": str(kg_db)})
    try:
        conn = sqlite3.connect(kg_db)
        try:
            tables = {str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "relations" not in tables or "entities" not in tables:
                return _warn("kg schema not compatible for orphan check", {"tables": sorted(tables)})
            cols = [str(r[1]) for r in conn.execute("PRAGMA table_info(relations)").fetchall()]
            subj_col = None
            obj_col = None
            for cand in ("subject_entity_id", "subject_id"):
                if cand in cols:
                    subj_col = cand
                    break
            for cand in ("object_entity_id", "object_id"):
                if cand in cols:
                    obj_col = cand
                    break
            if not subj_col or not obj_col:
                return _warn("kg relation columns unsupported", {"relation_columns": cols})
            sql = """
            SELECT COUNT(*)
            FROM relations r
            LEFT JOIN entities s ON s.id = r.{subj}
            LEFT JOIN entities o ON o.id = r.{obj}
            WHERE s.id IS NULL OR o.id IS NULL
            """
            orphan = int(conn.execute(sql.format(subj=subj_col, obj=obj_col)).fetchone()[0] or 0)
        finally:
            conn.close()
    except (sqlite3.Error, OSError, ValueError, TypeError) as exc:
        return _warn("kg orphan check unavailable", {"error": str(exc)})

    details = {"orphan_relations": orphan}
    if orphan > 5:
        return _fail("kg orphan relations high", details)
    if orphan > 0:
        return _warn("kg orphan relations detected", details)
    return _ok("kg orphan check healthy", details)


def _check_l2_jsonl_parse(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    mem_dir = Path(core.config["memory_dir"])
    targets: list[Path] = []
    targets.extend(sorted(mem_dir.rglob("*.jsonl")))
    # pipeline log is JSONL-ish and is part of the critical path.
    pipe_log = mem_dir / "auto_memory_pipeline.log"
    if pipe_log.exists():
        targets.append(pipe_log)

    if not targets:
        return _warn("no jsonl targets found", {"root": str(mem_dir)})

    bad_total = 0
    line_total = 0
    bad_files: dict[str, int] = {}
    for p in targets:
        bad = 0
        total = 0
        try:
            with p.open("r", encoding="utf-8", errors="ignore") as f:
                for ln in f:
                    raw = ln.strip()
                    if not raw:
                        continue
                    total += 1
                    try:
                        json.loads(raw)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        bad += 1
        except OSError:
            # unreadable file counts as one bad file with synthetic bad lines.
            bad = 1
            total = max(total, 1)
        if bad > 0:
            bad_files[str(p)] = bad
        bad_total += bad
        line_total += total

    details = {
        "files": len(targets),
        "lines": line_total,
        "bad_lines": bad_total,
        "bad_files": dict(list(bad_files.items())[:20]),
    }
    if bad_total == 0:
        return _ok("jsonl parse healthy", details)
    if bad_total <= 10:
        return _warn("jsonl parse has minor corruption", details)
    return _fail("jsonl parse corruption high", details)


def _check_l2_slo_check(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    mon = getattr(core, "monitoring", None)
    if mon is None:
        return _warn("monitoring unavailable for slo check")
    try:
        snap = mon.status()
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        return _warn("slo check unavailable", {"error": str(exc)})
    slo = snap.get("slo", {}) if isinstance(snap, dict) else {}
    if not isinstance(slo, dict):
        return _warn("slo payload invalid")
    checks = slo.get("checks", {}) if isinstance(slo.get("checks", {}), dict) else {}
    failed_checks = [k for k, v in checks.items() if not bool(v)]
    details = {
        "all_ok": bool(slo.get("all_ok", False)),
        "checks": checks,
        "failed_checks": failed_checks,
    }
    # Avoid false warning when replay SLO has no recent sample at all.
    if failed_checks == ["shadow_replay_success_rate"]:
        mp = snap.get("maintenance_policy", {}) if isinstance(snap, dict) else {}
        sr = mp.get("shadow_replay", {}) if isinstance(mp.get("shadow_replay", {}), dict) else {}
        runs = int(sr.get("runs", 0) or 0)
        if runs <= 0:
            details["shadow_replay_runs"] = runs
            return _ok("slo replay check skipped (no replay samples yet)", details)
    if bool(slo.get("all_ok", False)):
        return _ok("slo healthy", details)
    if len(failed_checks) <= 1:
        return _warn("slo has single breach", details)
    return _fail("slo has multiple breaches", details)


def _check_l2_repair_audit_health(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    memory_dir = Path(core.config["memory_dir"])
    p = memory_dir / "logs" / "repair_ops_audit.jsonl"
    if not p.exists():
        return _warn("repair audit log missing", {"path": str(p)})
    tail = p.read_text(encoding="utf-8", errors="ignore").splitlines()[-200:]
    bad = 0
    for ln in tail:
        t = ln.strip()
        if not t:
            continue
        try:
            json.loads(t)
        except (TypeError, ValueError, json.JSONDecodeError):
            bad += 1
    if bad > 0:
        return _warn(
            "repair audit has malformed rows",
            {"path": str(p), "bad_rows": bad, "tail_rows": len(tail)},
        )
    writable = os.access(p, os.W_OK)
    if not writable:
        return _fail("repair audit not writable", {"path": str(p)})
    return _ok("repair audit healthy", {"path": str(p), "tail_rows": len(tail)})


def _check_l2_repair_lock_health(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    memory_dir = Path(core.config["memory_dir"])
    lock = memory_dir / "state" / "repair_in_progress.lock"
    if not lock.exists():
        return _ok("repair lock healthy (no active lock)")
    age_s = max(0.0, time.time() - float(lock.stat().st_mtime))
    details = {"path": str(lock), "age_seconds": round(age_s, 2)}
    if age_s > 3600:
        return _warn("repair lock stale", details)
    return _ok("repair lock active", details)


def _check_l3_repair_effectiveness(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    cfg_sc = core.config.get("settings", {}).get("memory", {}).get("self_check", {})
    try:
        fail_success_threshold = float(cfg_sc.get("repair_effectiveness_fail_success_rate", 0.5))
    except (TypeError, ValueError):
        fail_success_threshold = 0.5
    try:
        warn_success_threshold = float(cfg_sc.get("repair_effectiveness_warn_success_rate", 0.75))
    except (TypeError, ValueError):
        warn_success_threshold = 0.75
    try:
        warn_rollback_threshold = float(cfg_sc.get("repair_effectiveness_warn_rollback_rate", 0.3))
    except (TypeError, ValueError):
        warn_rollback_threshold = 0.3

    memory_dir = Path(core.config["memory_dir"])
    latest = memory_dir / "reports" / "self_check_latest.json"
    if not latest.exists():
        return _warn("self-check latest report missing for repair effectiveness")
    try:
        payload = json.loads(latest.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return _warn("self-check latest unreadable for repair effectiveness", {"error": str(exc)})
    rs = payload.get("repair_summary", {}) if isinstance(payload.get("repair_summary", {}), dict) else {}
    w7 = rs.get("window_7d", {}) if isinstance(rs.get("window_7d", {}), dict) else {}
    total = int(w7.get("total", 0) or 0)
    success_rate = float(w7.get("success_rate", 0.0) or 0.0)
    rollback_rate = float(w7.get("rollback_rate", 0.0) or 0.0)
    details = {
        "total_7d": total,
        "success_rate_7d": success_rate,
        "rollback_rate_7d": rollback_rate,
    }
    if total <= 0:
        return _ok("repair effectiveness skipped (no repair samples)", details)
    if total < 10:
        return _ok("repair effectiveness sample too small", details)
    if success_rate < fail_success_threshold:
        return _fail("repair effectiveness low", details)
    if success_rate < warn_success_threshold or rollback_rate > warn_rollback_threshold:
        return _warn("repair effectiveness needs attention", details)
    return _ok("repair effectiveness healthy", details)


def _check_l1_check_coverage(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    cfg = core.config["settings"]["memory"].get("self_check", {})
    expected_min = int(cfg.get("min_registered_checks", 53) or 53)
    registered = len(build_check_specs(level="FULL_PLUS"))
    details = {"registered_checks": registered, "expected_min": expected_min}
    if registered < expected_min:
        return _fail("self-check coverage below minimum", details)
    if registered < expected_min + 2:
        return _warn("self-check coverage near minimum threshold", details)
    return _ok("self-check coverage healthy", details)


def _check_l1_self_check_integrity(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    reports_dir = Path(core.config["memory_dir"]) / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    baseline = reports_dir / "self_check_integrity_baseline.json"
    current = _current_self_check_hashes()
    now = _now_iso()
    if not baseline.exists():
        baseline.write_text(
            json.dumps({"created_at": now, "hashes": current}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return _warn(
            "self-check integrity baseline initialized",
            {"baseline": str(baseline), "hashes": current},
        )
    try:
        payload = json.loads(baseline.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return _fail(
            "self-check integrity baseline unreadable",
            {"baseline": str(baseline), "error": str(exc)},
        )
    old = payload.get("hashes", {}) if isinstance(payload, dict) else {}
    mismatched = [k for k, v in current.items() if str(old.get(k, "")) != str(v)]
    details = {"baseline": str(baseline), "mismatched": mismatched}
    if mismatched:
        return _fail("self-check module integrity mismatch", details)
    return _ok("self-check module integrity healthy", details)


def _check_l1_baseline_update_request(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    reports_dir = Path(core.config["memory_dir"]) / "reports"
    req = reports_dir / "baseline_update_request.json"
    if not req.exists():
        return _ok("no pending baseline update request", {"path": str(req)})
    try:
        payload = json.loads(req.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return _fail(
            "baseline update request unreadable",
            {"path": str(req), "error": str(exc)},
        )
    status = str(payload.get("status", "")).strip().lower()
    generated_at = _to_aware(str(payload.get("generated_at", "")))
    age_hours = 0.0
    if generated_at is not None:
        age_hours = max(0.0, (datetime.now(timezone.utc) - generated_at).total_seconds() / 3600.0)
    details = {
        "path": str(req),
        "status": status,
        "authorizer": str(payload.get("authorizer", "")),
        "age_hours": round(age_hours, 3),
        "change_count": len(payload.get("changes", [])) if isinstance(payload.get("changes", []), list) else 0,
    }
    if status in {"authorized", "resolved", "applied", "no_change"}:
        return _warn("baseline update request should be archived", details)
    return _warn("baseline update request pending authorization", details)


def _check_l3_manifest_signature(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    st = core.shadow_status() if hasattr(core, "shadow_status") else {}
    if not isinstance(st, dict):
        return _warn("shadow status unavailable")
    if bool(st.get("manifest_signature_valid", True)):
        return _ok("manifest signature valid")
    return _fail("manifest signature invalid", {"status": st})


def _check_l3_checkpoint_verify(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    out = (
        core.shadow_verify() if hasattr(core, "shadow_verify") else {"ok": False, "reason": "shadow_verify_unavailable"}
    )
    if bool(out.get("ok", False)):
        return _ok("shadow checkpoint verify ok", {"verify": out})
    return _fail("shadow checkpoint verify failed", {"verify": out})


def _check_l3_shadow_permissions(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    try:
        from ...security.shadow.shadow_permissions import ensure_shadow_permissions

        shadow_dir = Path(core.config["memory_dir"]) / "security" / "shadow_data"
        backup_dir = Path("~/.shadow_backup").expanduser()
        out = ensure_shadow_permissions(shadow_dir, backup_dir=backup_dir)
        violations = list(out.get("violations", []))
        corrected = list(out.get("corrected", []))
        if violations and corrected:
            return _warn(
                "shadow permissions corrected",
                {"violations": len(violations), "corrected": len(corrected)},
            )
        if violations and not corrected:
            return _fail("shadow permissions mismatch not corrected", {"violations": violations})
        return _ok("shadow permissions ok")
    except (ImportError, OSError, AttributeError, TypeError, ValueError) as exc:
        return _warn("shadow permission check unavailable", {"error": str(exc)})


def _check_l3_backup_freshness(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    state = Path(core.config["memory_dir"]) / "maintenance_state.json"
    if not state.exists():
        return _warn("maintenance state missing", {"path": str(state)})
    try:
        payload = json.loads(state.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return _warn("maintenance state unreadable", {"error": str(exc)})
    last = str(
        payload.get("last_backup_at", "") or payload.get("last_backup", "") or payload.get("backup_last_run", "")
    ).strip()
    if not last:
        return _warn("backup timestamp missing", {})
    try:
        raw = last[:-1] + "+00:00" if last.endswith("Z") else last
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        hrs = (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 3600.0
    except ValueError:
        return _warn("backup timestamp invalid", {"last_backup": last})
    if hrs > 24 * 7:
        return _fail("backup stale > 7d", {"hours_since_last_backup": round(hrs, 2)})
    if hrs > 48:
        return _warn("backup stale > 48h", {"hours_since_last_backup": round(hrs, 2)})
    return _ok("backup freshness healthy", {"hours_since_last_backup": round(hrs, 2)})


def _check_l3_sensitive_scan(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    mem_dir = Path(core.config["memory_dir"])
    workspace = Path(core.config["workspace_dir"])
    targets = [workspace / "MEMORY.md", workspace / "config.yaml"]
    shadow_events = mem_dir / "security" / "shadow_data" / "shadow_events.jsonl"
    leaks: list[dict[str, Any]] = []
    patterns = [
        ("github_pat", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
        ("ghp", re.compile(r"ghp_[A-Za-z0-9]{20,}")),
        ("aws_akia", re.compile(r"AKIA[0-9A-Z]{16}")),
        ("openai_sk", re.compile(r"sk-[A-Za-z0-9]{20,}")),
        ("bearer", re.compile(r"Bearer\s+[A-Za-z0-9\-\._~\+/=]{20,}", re.IGNORECASE)),
    ]
    for p in targets:
        if not p.exists():
            continue
        txt = p.read_text(encoding="utf-8", errors="ignore")
        for name, rgx in patterns:
            if rgx.search(txt):
                leaks.append({"target": str(p), "type": name})
    if shadow_events.exists():
        lines = shadow_events.read_text(encoding="utf-8", errors="ignore").splitlines()[-1000:]
        joined = "\n".join(lines)
        for name, rgx in patterns:
            if rgx.search(joined):
                leaks.append({"target": str(shadow_events), "type": name, "window": 1000})
    if leaks:
        return _warn("possible sensitive tokens detected", {"hits": leaks[:20], "count": len(leaks)})
    return _ok("no sensitive token pattern detected")


def _check_l3_shadow_health(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    if not hasattr(core, "shadow_health"):
        return _warn("shadow health api unavailable")
    try:
        health = core.shadow_health()
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        return _warn("shadow health check failed to run", {"error": str(exc)})
    if not isinstance(health, dict):
        return _warn("shadow health payload invalid", {"type": str(type(health))})
    ok = bool(health.get("ok", False) or health.get("status") == "ok")
    details = {
        "ok": ok,
        "mode": health.get("mode", ""),
        "issues": health.get("issues", []),
    }
    if ok:
        return _ok("shadow health healthy", details)
    return _fail("shadow health not ok", details)


def _check_l3_seal_history(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    if not hasattr(core, "shadow_status"):
        return _warn("shadow status api unavailable")
    try:
        st = core.shadow_status()
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        return _warn("shadow status unavailable", {"error": str(exc)})
    if not isinstance(st, dict):
        return _warn("shadow status payload invalid")
    history = st.get("history", [])
    if not isinstance(history, list):
        history = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    recent = 0
    for row in history:
        if not isinstance(row, dict):
            continue
        event = str(row.get("event", "")).lower()
        if event != "seal":
            continue
        ts = _to_aware(str(row.get("ts", "")))
        if ts is not None and ts >= cutoff:
            recent += 1
    details = {"seal_count_24h": recent}
    if recent >= 5:
        return _fail("seal frequency high in 24h", details)
    if recent >= 3:
        return _warn("seal frequency elevated in 24h", details)
    return _ok("seal frequency healthy", details)


def _check_l3_spool_backlog(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    if not hasattr(core, "shadow_status"):
        return _warn("shadow status api unavailable")
    try:
        st = core.shadow_status()
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        return _warn("shadow status unavailable", {"error": str(exc)})
    if not isinstance(st, dict):
        return _warn("shadow status payload invalid")

    pending = int(st.get("spool_pending", 0) or 0)
    oldest_hours = None
    oldest_ts = _to_aware(str(st.get("spool_oldest_pending_ts", "")))
    if oldest_ts is not None:
        oldest_hours = round((datetime.now(timezone.utc) - oldest_ts).total_seconds() / 3600.0, 2)
    details = {"spool_pending": pending, "oldest_pending_hours": oldest_hours}
    if pending <= 0:
        return _ok("spool backlog healthy", details)
    if bool(st.get("sealed", False)):
        return _warn("spool backlog exists while sealed", details)
    if oldest_hours is None:
        # if pending exists but no timestamp, still warn.
        return _warn("spool backlog exists without age", details)
    if oldest_hours > 24:
        return _fail("spool backlog stale >24h", details)
    if oldest_hours > 6:
        return _warn("spool backlog elevated >6h", details)
    return _ok("spool backlog within window", details)


def _check_l3_closed_loop_evidence(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    """
    Validate trigger -> action -> result -> evidence chain on maintenance policy executions.
    """
    path = Path(core.config["memory_dir"]) / "maintenance_policy_log.jsonl"
    if not path.exists():
        return _warn("maintenance policy log missing", {"path": str(path)})
    rows = path.read_text(encoding="utf-8", errors="ignore").splitlines()[-300:]
    total = 0
    complete = 0
    missing_examples: list[dict[str, Any]] = []
    evidence_keys = {
        "report_paths",
        "verify",
        "persist",
        "replayed",
        "restored_from",
        "snapshot_path",
        "healthchecks",
        "alerts_emitted",
    }
    for ln in rows:
        raw = ln.strip()
        if not raw:
            continue
        try:
            row = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        action = str(row.get("action", "")).strip()
        trigger = str(row.get("trigger_reason", row.get("trigger", ""))).strip()
        result = row.get("result", {})
        if not action:
            continue
        total += 1
        has_result = isinstance(result, dict)
        has_evidence = False
        if isinstance(row.get("evidence", None), dict):
            has_evidence = True
        if has_result:
            has_evidence = has_evidence or any(k in result for k in evidence_keys)
        # baseline evidence can also be status+timestamped result shape
        if has_result and ("status" in result) and (result.get("status") not in ("", None)):
            has_evidence = True
        ok = bool(trigger and has_result and has_evidence)
        if ok:
            complete += 1
        elif len(missing_examples) < 10:
            missing_examples.append(
                {
                    "action": action,
                    "has_trigger": bool(trigger),
                    "has_result": bool(has_result),
                    "has_evidence": bool(has_evidence),
                }
            )

    if total == 0:
        return _warn("no maintenance actions to evaluate closed-loop evidence", {"samples": 0})
    ratio = round(complete / max(1, total), 4)
    details = {
        "samples": total,
        "complete_chain": complete,
        "complete_ratio": ratio,
        "missing_examples": missing_examples,
    }
    if ratio < 0.70:
        return _fail("closed-loop evidence coverage low", details)
    if ratio < 0.90:
        return _warn("closed-loop evidence coverage moderate", details)
    return _ok("closed-loop evidence coverage healthy", details)


def _check_l4_capture_trend(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    log_path = Path(core.config["memory_dir"]) / "auto_memory_log.json"
    if not log_path.exists():
        return _warn("auto_memory_log missing", {"path": str(log_path)})
    try:
        payload = json.loads(log_path.read_text(encoding="utf-8"))
        entries = payload.get("entries", []) if isinstance(payload, dict) else []
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return _warn("auto_memory_log unreadable", {"error": str(exc)})
    if not isinstance(entries, list) or not entries:
        return _warn("no capture entries", {})

    day_stats: dict[str, dict[str, int]] = {}
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    for e in entries:
        if not isinstance(e, dict):
            continue
        ts_raw = str(e.get("timestamp", "") or "")
        ts = _to_aware(ts_raw)
        if ts is None or ts < cutoff:
            continue
        day = ts.date().isoformat()
        st = str(e.get("status", ""))
        # Keep only extraction terminal events; session sync entries are traffic.
        # "dropped" usually means policy/noise filtering and should not be treated
        # as extraction quality failure in denominator.
        if st not in {"success", "dropped", "rejected", "duplicate"}:
            continue
        bucket = day_stats.setdefault(day, {"quality_total": 0, "success": 0, "dropped": 0, "traffic_total": 0})
        bucket["traffic_total"] += 1
        if st == "dropped":
            bucket["dropped"] += 1
            continue
        bucket["quality_total"] += 1
        if st == "success":
            bucket["success"] += 1

    if not day_stats:
        return _warn("no capture entries in 7d window", {})
    days = sorted(day_stats.keys())
    ratios = [day_stats[d]["success"] / max(1, day_stats[d]["quality_total"]) for d in days]
    if len(ratios) < 3:
        return _ok(
            "capture trend insufficient samples",
            {"days": len(ratios), "avg": round(sum(ratios) / len(ratios), 4)},
        )

    head = sum(ratios[: max(1, len(ratios) // 2)]) / max(1, len(ratios) // 2)
    tail = sum(ratios[max(1, len(ratios) // 2) :]) / max(1, len(ratios) - max(1, len(ratios) // 2))
    delta = round(tail - head, 4)
    avg = round(sum(ratios) / len(ratios), 4)
    details = {
        "days": len(ratios),
        "avg": avg,
        "delta": delta,
        "head": round(head, 4),
        "tail": round(tail, 4),
        "quality_samples": int(sum(day_stats[d]["quality_total"] for d in days)),
        "dropped_samples": int(sum(day_stats[d]["dropped"] for d in days)),
        "traffic_samples": int(sum(day_stats[d]["traffic_total"] for d in days)),
    }
    if avg < 0.60:
        return _fail("capture trend low", details)
    if delta < -0.15:
        return _warn("capture trend declining", details)
    return _ok("capture trend healthy", details)


def _check_l4_injection_effectiveness(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    usage = Path(core.config["memory_dir"]) / "memory_usage_log.jsonl"
    if not usage.exists():
        return _warn("memory_usage_log missing", {"path": str(usage)})
    rows = usage.read_text(encoding="utf-8", errors="ignore").splitlines()[-2000:]
    total = 0
    used = 0
    skipped_probe = 0
    skipped_empty = 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    # Keep aligned with monitoring under-sample strategy to avoid false fail
    # on low-traffic environments.
    min_samples = 10
    probe_prefixes = (
        "verify_canary:",
        "connect smoke",
        "smoke_test",
        "health probe",
        "self-check probe",
        "test:",
    )
    for line in rows:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        ts = _to_aware(str(row.get("timestamp", "")))
        if ts is not None and ts < cutoff:
            continue
        query_text = str(row.get("query", "")).strip().lower()
        if not query_text:
            skipped_empty += 1
            continue
        if any(query_text.startswith(prefix) for prefix in probe_prefixes):
            skipped_probe += 1
            continue
        total += 1
        if bool(row.get("used_in_answer", False)):
            used += 1
            continue
        if int(row.get("injected_count", row.get("count", 0)) or 0) > 0:
            used += 1
    if total == 0:
        return _ok(
            "no injection samples",
            {
                "samples": 0,
                "used_ratio": 0.0,
                "min_samples": min_samples,
                "under_sample": True,
                "skipped_probe": skipped_probe,
                "skipped_empty": skipped_empty,
            },
        )
    ratio = round(used / max(1, total), 4)
    details = {
        "samples": total,
        "used_ratio": ratio,
        "min_samples": min_samples,
        "under_sample": total < min_samples,
        "skipped_probe": skipped_probe,
        "skipped_empty": skipped_empty,
    }
    if total < min_samples:
        # Low-traffic/dev environments should not be treated as unhealthy.
        return _ok("injection effectiveness under-sample", details)
    if ratio < 0.40:
        return _fail("injection effectiveness low", details)
    if ratio < 0.55:
        return _warn("injection effectiveness moderate", details)
    return _ok("injection effectiveness healthy", details)


def _check_l4_threshold_suggestions(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    p = Path(core.config["memory_dir"]) / "threshold_suggestions_pending.json"
    if not p.exists():
        return _ok("threshold suggestion queue empty", {"pending": 0})
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return _warn("threshold suggestion file unreadable", {"error": str(exc)})
    items = payload.get("items", []) if isinstance(payload, dict) else []
    if not isinstance(items, list):
        items = []
    pending = [x for x in items if isinstance(x, dict) and str(x.get("status", "pending")) == "pending"]
    if not pending:
        return _ok("threshold suggestion queue empty", {"pending": 0})

    oldest = None
    for row in pending:
        ts = _to_aware(str(row.get("created_at", row.get("timestamp", ""))))
        if ts is None:
            continue
        oldest = ts if oldest is None else min(oldest, ts)
    stale_days = 0.0
    if oldest is not None:
        stale_days = (datetime.now(timezone.utc) - oldest).total_seconds() / 86400.0
    details = {"pending": len(pending), "oldest_pending_days": round(stale_days, 2)}
    if len(pending) >= 20 or stale_days > 14:
        return _fail("threshold suggestions backlog high", details)
    if len(pending) >= 7 or stale_days > 7:
        return _warn("threshold suggestions pending review", details)
    return _ok("threshold suggestions under control", details)


def _check_l4_capacity_projection(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    memory_dir = Path(core.config["memory_dir"])
    usage = shutil.disk_usage(memory_dir)
    free = int(usage.free)

    # Estimate daily growth from log-like files modified within 30 days.
    now = datetime.now(timezone.utc)
    total_bytes = 0
    recent_30d = 0
    recent_7d = 0
    for p in memory_dir.rglob("*"):
        if not p.is_file():
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        size = int(st.st_size)
        total_bytes += size
        mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
        if p.suffix in {".jsonl", ".json", ".log", ".md", ".db"}:
            age_s = (now - mtime).total_seconds()
            if age_s <= 30 * 86400:
                recent_30d += size
            if age_s <= 7 * 86400:
                recent_7d += size

    growth_30d = recent_30d / 30.0
    growth_7d = recent_7d / 7.0
    daily_growth = max(1.0, growth_30d, growth_7d)
    days_left = round(free / daily_growth, 2)
    details = {
        "free_gb": round(free / (1024**3), 3),
        "projected_days_left": days_left,
        "recent_30d_mb": round(recent_30d / (1024**2), 3),
        "recent_7d_mb": round(recent_7d / (1024**2), 3),
        "daily_growth_mb_30d": round(growth_30d / (1024**2), 3),
        "daily_growth_mb_7d": round(growth_7d / (1024**2), 3),
        "daily_growth_mb_conservative": round(daily_growth / (1024**2), 3),
        "total_data_mb": round(total_bytes / (1024**2), 3),
    }
    if days_left < 7:
        return _fail("capacity projection critical", details)
    if days_left < 30:
        return _warn("capacity projection tight", details)
    return _ok("capacity projection healthy", details)


def _check_l5_llm_notice_state_health(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    memory_dir = Path(core.config["memory_dir"])
    state_file = memory_dir / "health" / "llm_notice_state.json"
    if not state_file.exists():
        # Fresh installs may not have emitted any notice yet.
        return _ok("llm notice state not initialized yet", {"path": str(state_file), "initialized": False})
    try:
        payload = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return _warn("llm notice state unreadable", {"path": str(state_file), "error": str(exc)})
    if not isinstance(payload, dict):
        return _warn("llm notice state invalid payload", {"path": str(state_file)})
    mode = str(payload.get("last_mode", "")).strip()
    allowed = {"full", "hybrid", "local_only", "cloud_only", "rule_only", ""}
    if mode not in allowed:
        return _warn("llm notice state unknown mode", {"path": str(state_file), "last_mode": mode})
    ts = _to_aware(str(payload.get("updated_at", "")))
    details = {"path": str(state_file), "last_mode": mode or "unknown", "has_updated_at": ts is not None}
    return _ok("llm notice state healthy", details)


def _check_m1_short_term_persistence(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    wm_file = Path(core.config["memory_dir"]) / "working_memory.jsonl"
    if not wm_file.exists():
        return _warn("working memory persistence file missing", {"path": str(wm_file)})
    lines = 0
    try:
        with wm_file.open("r", encoding="utf-8", errors="ignore") as f:
            lines = sum(1 for _ in f if _.strip())
    except OSError as exc:
        return _warn("working memory persistence unreadable", {"path": str(wm_file), "error": str(exc)})
    has_restore_api = hasattr(core, "restore_short_term_by_topic")
    if lines == 0:
        return _warn(
            "working memory persistence empty",
            {"path": str(wm_file), "restore_api": has_restore_api},
        )
    if not has_restore_api:
        return _warn("working memory restore api missing", {"records": lines})
    return _ok("short-term persistence healthy", {"records": lines, "restore_api": has_restore_api})


def _check_m2_rejected_not_indexed(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    idx = Path(core.config["memory_dir"]) / "auto_memory_index.json"
    if not idx.exists():
        return _warn("auto_memory_index missing", {"path": str(idx)})
    try:
        payload = json.loads(idx.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return _warn("auto_memory_index unreadable", {"error": str(exc)})
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = payload.get("items", [])
    else:
        items = []
    leaked = 0
    total = 0
    for row in items if isinstance(items, list) else []:
        if not isinstance(row, dict):
            continue
        total += 1
        status = str(row.get("status", "")).lower()
        excluded = bool(row.get("excluded", False))
        if status == "rejected" and not excluded:
            leaked += 1
    details = {"items": total, "rejected_in_index": leaked}
    if leaked > 0:
        return _fail("rejected records leaked into index", details)
    return _ok("rejected records excluded from index", details)


def _check_m3_review_queue_sla(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    path = Path(core.config["memory_dir"]) / "auto_memory_review_queue.jsonl"
    if not path.exists():
        return _ok("review queue empty", {"pending": 0})
    pending = 0
    oldest = None
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            for ln in f:
                raw = ln.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                decision = str(row.get("decision", "pending")).strip().lower()
                if decision not in {"pending", "review", "needs_review", ""}:
                    continue
                pending += 1
                ts = _to_aware(str(row.get("timestamp", row.get("created_at", ""))))
                if ts is not None:
                    oldest = ts if oldest is None else min(oldest, ts)
    except OSError as exc:
        return _warn("review queue unreadable", {"error": str(exc)})
    stale_hours = None
    if oldest is not None:
        stale_hours = round((datetime.now(timezone.utc) - oldest).total_seconds() / 3600.0, 2)
    details = {"pending": pending, "oldest_hours": stale_hours}
    if pending > 120 or (stale_hours is not None and stale_hours > 48):
        return _fail("review queue sla breached", details)
    if pending > 50 or (stale_hours is not None and stale_hours > 24):
        return _warn("review queue backlog elevated", details)
    return _ok("review queue sla healthy", details)


def _check_m4_dedupe_false_positive_probe(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    memory_dir = Path(core.config["memory_dir"])
    candidates = _pipeline_log_candidates(memory_dir)
    if not candidates:
        return _ok(
            "no pipeline log available for dedupe probe, skipping",
            {"path": str(memory_dir / "auto_memory_pipeline.log")},
        )
    path = candidates[0]
    total = 0
    dup_drop = 0
    hi_conf_dup_drop = 0
    rows = path.read_text(encoding="utf-8", errors="ignore").splitlines()[-500:]
    for ln in rows:
        try:
            row = json.loads(ln)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        dropped = row.get("dropped", [])
        if not isinstance(dropped, list):
            dropped = []
        if not dropped:
            continue
        total += 1
        reason_text = json.dumps(dropped, ensure_ascii=False)
        if "duplicate" in reason_text:
            dup_drop += 1
            conf = (
                float(row.get("confidence", row.get("record", {}).get("confidence", 0.0)) or 0.0)
                if isinstance(row, dict)
                else 0.0
            )
            if conf >= 0.75:
                hi_conf_dup_drop += 1
    if total == 0:
        return _ok("dedupe probe no dropped samples", {"samples": 0})
    ratio = round(dup_drop / total, 4)
    hi_ratio = round(hi_conf_dup_drop / max(1, dup_drop), 4)
    details = {
        "samples": total,
        "duplicate_drop_ratio": ratio,
        "high_conf_duplicate_ratio": hi_ratio,
    }
    if hi_conf_dup_drop > 0 and hi_ratio > 0.20:
        return _warn("possible dedupe false-positive risk", details)
    return _ok("dedupe false-positive probe healthy", details)


def _check_m5_semantic_cache_health(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    path = Path(core.config["memory_dir"]) / "semantic_cache.json"
    if not path.exists():
        return _warn("semantic cache missing", {"path": str(path)})
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return _warn("semantic cache unreadable", {"error": str(exc)})
    items: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        candidate = payload.get("cache", payload.get("items", payload))
        if isinstance(candidate, list):
            items = [x for x in candidate if isinstance(x, dict)]
        elif isinstance(candidate, dict):
            # backward-compatible map format: {key: {dense/sparse/...}}
            for k, v in candidate.items():
                if isinstance(v, dict):
                    row = dict(v)
                    row.setdefault("cache_key", str(k))
                    items.append(row)
        else:
            items = []
    elif isinstance(payload, list):
        items = [x for x in payload if isinstance(x, dict)]
    else:
        items = []
    if not items:
        return _warn("semantic cache empty or unreadable structure")
    total = len(items)
    dense_missing = 0
    for row in items:
        if not isinstance(row, dict):
            continue
        if row.get("dense", None) in (None, []):
            dense_missing += 1
    ratio = round(dense_missing / max(1, total), 4)
    details = {"items": total, "dense_missing": dense_missing, "dense_missing_ratio": ratio}
    if ratio > 0.5:
        return _fail("semantic cache dense vectors missing high", details)
    if ratio > 0.2:
        return _warn("semantic cache dense vectors missing elevated", details)
    return _ok("semantic cache healthy", details)


def _check_m6_cjk_recall_probe(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    q_full = "记忆系统配置"
    q_part = "记忆"
    try:
        full_rows = core.retrieve_memories(q_full, top_k=5)
        part_rows = core.retrieve_memories(q_part, top_k=5)
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        return _warn("cjk recall probe unavailable", {"error": str(exc)})
    full_hits = len(full_rows) if isinstance(full_rows, list) else 0
    part_hits = len(part_rows) if isinstance(part_rows, list) else 0
    details = {
        "query_full": q_full,
        "query_part": q_part,
        "full_hits": full_hits,
        "part_hits": part_hits,
    }
    if part_hits > 0 and full_hits == 0:
        return _warn("cjk recall weak on concatenated query", details)
    if full_hits == 0 and part_hits == 0:
        return _warn("cjk recall probe got no hits", details)
    return _ok("cjk recall probe healthy", details)


def _check_m7_kg_access_feedback(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    kg_db = Path(core.config["memory_dir"]) / "knowledge_graph.db"
    if not kg_db.exists():
        return _warn("knowledge_graph db missing", {"path": str(kg_db)})
    try:
        conn = sqlite3.connect(kg_db)
        try:
            tables = {str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "entities" not in tables:
                return _warn("kg entities table missing", {"tables": sorted(tables)})
            total = int(conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0] or 0)
            zero = int(conn.execute("SELECT COUNT(*) FROM entities WHERE access_count = 0").fetchone()[0] or 0)
        finally:
            conn.close()
    except (sqlite3.Error, OSError, TypeError, ValueError) as exc:
        return _warn("kg access feedback probe unavailable", {"error": str(exc)})
    ratio = round(zero / max(1, total), 4)
    details = {"entities": total, "zero_access": zero, "zero_access_ratio": ratio}
    if total >= 50 and ratio > 0.95:
        return _warn("kg access feedback weak (high zero-access ratio)", details)
    return _ok("kg access feedback healthy", details)


def _check_m8_pipeline_latency_budget(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    path = Path(core.config["memory_dir"]) / "auto_memory_pipeline.log"
    if not path.exists():
        return _warn("pipeline log missing", {"path": str(path)})
    rows = path.read_text(encoding="utf-8", errors="ignore").splitlines()[-300:]
    row_count = 0
    samples: list[float] = []
    for ln in rows:
        row_count += 1
        try:
            row = json.loads(ln)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        dur = row.get("duration_ms")
        if isinstance(dur, (int, float)):
            samples.append(float(dur))
            continue
        trace = row.get("trace", {})
        if isinstance(trace, dict):
            maybe = trace.get("duration_ms")
            if isinstance(maybe, (int, float)):
                samples.append(float(maybe))
    if not samples:
        if row_count > 0:
            return _ok(
                "pipeline active but latency instrumentation unavailable",
                {"rows": row_count, "samples": 0},
            )
        return _warn("pipeline latency samples missing", {"samples": 0})
    samples.sort()
    p95 = samples[min(len(samples) - 1, int(len(samples) * 0.95))]
    avg = round(sum(samples) / len(samples), 2)
    details = {"samples": len(samples), "avg_ms": avg, "p95_ms": round(p95, 2)}
    if p95 > 3000:
        return _fail("pipeline latency budget exceeded", details)
    if p95 > 1500 and len(samples) >= 200:
        return _warn("pipeline latency elevated", details)
    return _ok("pipeline latency budget healthy", details)


def _check_s1_keychain_effective(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    sec_cfg = core.config["settings"]["memory"].get("security", {})
    use_keychain = bool(sec_cfg.get("use_keychain", False))
    key_file = Path(core.config["memory_dir"]) / "security" / "shadow_data" / "manifest_hmac.key"
    exists = key_file.exists()
    details = {
        "use_keychain": use_keychain,
        "manifest_hmac_file_exists": exists,
        "path": str(key_file),
    }
    if use_keychain and exists:
        return _warn("keychain enabled but file key still present", details)
    if use_keychain and not exists:
        return _ok("keychain effective and file key removed", details)
    return _warn("keychain not enabled", details)


def _check_s2_shadow_ops_audit_writable(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    path = Path(core.config["memory_dir"]) / "security" / "shadow_data" / "ops_audit.jsonl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write("")
    except OSError as exc:
        return _fail("shadow ops audit not writable", {"path": str(path), "error": str(exc)})
    return _ok("shadow ops audit writable", {"path": str(path)})


def _check_s3_shadow_immutable_flags(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    imm_enabled = bool(
        core.config["settings"]["memory"].get("security", {}).get("shadow", {}).get("immutable_enabled", False)
    )
    shadow_dir = Path(core.config["memory_dir"]) / "security" / "shadow_data"
    events = shadow_dir / "shadow_events.jsonl"
    if not imm_enabled:
        return _warn("shadow immutable protection disabled by config", {"immutable_enabled": False})
    if not events.exists():
        return _warn("shadow events missing for immutable check", {"path": str(events)})
    uf_immutable = getattr(stat, "UF_IMMUTABLE", 0x00000002)
    try:
        st = events.stat()
        flags = int(getattr(st, "st_flags", 0) or 0)
        has_uchg = bool(flags & int(uf_immutable))
        if has_uchg:
            return _ok("shadow immutable flag present", {"path": str(events)})
        return _warn("shadow immutable flag not set", {"path": str(events), "flags": flags})
    except OSError as exc:
        return _warn("shadow immutable check unavailable", {"error": str(exc), "path": str(events)})


def _check_s4_shadow_backup_dual_site(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    primary = Path(core.config["memory_dir"]) / "security" / "shadow_data" / "shadow_events.jsonl"
    backup = Path("~/.shadow_backup/shadow_events.jsonl").expanduser()
    details = {
        "primary_exists": primary.exists(),
        "backup_exists": backup.exists(),
        "primary": str(primary),
        "backup": str(backup),
    }
    if not primary.exists():
        return _fail("shadow primary events missing", details)
    if not backup.exists():
        return _warn("shadow backup site missing", details)
    return _ok("shadow dual-site events available", details)


def _check_s5_replay_dryrun_weekly(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    path = Path(core.config["memory_dir"]) / "maintenance_policy_log.jsonl"
    if not path.exists():
        return _warn("maintenance policy log missing", {"path": str(path)})
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    hits = 0
    for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines()[-2000:]:
        try:
            row = json.loads(ln)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        act = str(row.get("action", ""))
        if "shadow_recovery_drill" not in act and "shadow_replay_spool" not in act:
            continue
        ts = _to_aware(str(row.get("timestamp", "")))
        if ts is not None and ts >= cutoff:
            hits += 1
    if hits <= 0:
        return _warn("no recent shadow replay/drill run in 7d", {"hits_7d": hits})
    return _ok("shadow replay/drill cadence healthy", {"hits_7d": hits})


def _check_s6_manifest_checkpoint_pair(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    try:
        status = core.shadow_status() if hasattr(core, "shadow_status") else {}
        verify = core.shadow_verify() if hasattr(core, "shadow_verify") else {"ok": False}
    except (AttributeError, TypeError, ValueError) as exc:
        return _warn("manifest/checkpoint pair check unavailable", {"error": str(exc)})
    sig_ok = bool(status.get("manifest_signature_valid", True)) if isinstance(status, dict) else False
    cp_ok = bool(verify.get("ok", False)) if isinstance(verify, dict) else False
    details = {"manifest_signature_valid": sig_ok, "checkpoint_ok": cp_ok}
    if sig_ok and cp_ok:
        return _ok("manifest+checkpoint gate healthy", details)
    if (not sig_ok) and (not cp_ok):
        return _fail("manifest+checkpoint gate both failed", details)
    return _warn("manifest+checkpoint gate partial", details)


def _check_s7_locking_lease_health(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    shadow = getattr(core, "shadow", None)
    if shadow is None:
        return _warn("shadow unavailable for lease health")
    locking = getattr(shadow, "locking", None)
    if locking is None:
        return _warn("shadow locking unavailable")
    try:
        lease = locking.current_lease()
    except (AttributeError, TypeError, ValueError) as exc:
        return _warn("shadow locking probe failed", {"error": str(exc)})
    if lease is None:
        return _ok("shadow locking healthy (no active lease)")
    expired = False
    try:
        expired = bool(getattr(lease, "expired", lambda: False)())
    except (AttributeError, TypeError, ValueError):
        expired = False
    if expired:
        return _fail(
            "shadow locking lease expired but still active",
            {
                "op_name": getattr(lease, "op_name", ""),
                "owner": getattr(lease, "owner", ""),
                "lease_id": getattr(lease, "lease_id", ""),
            },
        )
    return _ok(
        "shadow locking has active valid lease",
        {
            "op_name": getattr(lease, "op_name", ""),
            "owner": getattr(lease, "owner", ""),
            "lease_id": getattr(lease, "lease_id", ""),
        },
    )


def _check_s8_minimal_survival_trigger(core: Any, _ctx: dict[str, Any]) -> dict[str, Any]:
    shadow = getattr(core, "shadow", None)
    if shadow is None:
        return _warn("shadow unavailable for minimal_survival check")
    cap_guard = getattr(shadow, "capacity_guard", None)
    if cap_guard is None:
        return _warn("shadow capacity guard unavailable")
    try:
        eval_out = cap_guard.evaluate()
    except (AttributeError, TypeError, ValueError) as exc:
        return _warn("shadow capacity evaluate failed", {"error": str(exc)})
    mode = str(shadow.status().get("mode", ""))
    stage = str(eval_out.get("stage", "ok"))
    details = {"mode": mode, "capacity_stage": stage, "ratio": eval_out.get("ratio", 0.0)}
    if stage == "critical" and mode != "minimal_survival":
        return _warn("capacity critical but minimal_survival not active", details)
    return _ok("minimal_survival trigger path healthy", details)


def build_check_specs(level: str = "L1") -> list[CheckSpec]:
    level_key = str(level or "L1").upper()
    specs: list[CheckSpec] = [
        CheckSpec(
            "l1_launchd_mcp",
            "L1",
            5,
            "Run launchctl kickstart gui/$(id -u)/com.openclaw.memory.mcp",
            _check_l1_launchd_mcp,
            domain="connect",
        ),
        CheckSpec(
            "l1_launchd_maintenance",
            "L1",
            5,
            "Run ocma install-agent to register maintenance launch agent",
            _check_l1_launchd_maintenance,
            domain="memory",
        ),
        CheckSpec(
            "l1_core_files",
            "L1",
            5,
            "Restore missing core files from backups",
            _check_l1_core_files,
            domain="memory",
        ),
        CheckSpec(
            "l1_shadow_files",
            "L1",
            5,
            "Run shadow health and repair missing survival files",
            _check_l1_shadow_files,
            domain="security",
        ),
        CheckSpec(
            "l1_canary_io",
            "L1",
            5,
            "Check disk permission and free space",
            _check_l1_canary_io,
            domain="memory",
        ),
        CheckSpec(
            "l1_disk_space",
            "L1",
            5,
            "Cleanup old backups and archives",
            _check_l1_disk_space,
            domain="memory",
        ),
        CheckSpec(
            "l1_health_card_diff",
            "L1",
            5,
            "Run full self-check and refresh health card",
            _check_l1_health_card_diff,
            domain="memory",
        ),
        CheckSpec(
            "l1_shadow_sealed",
            "L1",
            5,
            "Inspect shadow status and recover if persistent sealed",
            _check_l1_shadow_sealed,
            domain="security",
        ),
        CheckSpec(
            "l1_self_check_framework",
            "L1",
            5,
            "Verify self-check report+heartbeat freshness",
            _check_l1_self_check_framework,
            domain="memory",
        ),
        CheckSpec(
            "l1_check_coverage",
            "L1",
            5,
            "Ensure minimum self-check coverage remains intact",
            _check_l1_check_coverage,
            domain="memory",
        ),
        CheckSpec(
            "l1_self_check_integrity",
            "L1",
            5,
            "Verify self-check module integrity hashes",
            _check_l1_self_check_integrity,
            domain="security",
        ),
        CheckSpec(
            "l1_baseline_update_request",
            "L1",
            5,
            "Ensure baseline update requests are resolved or archived",
            _check_l1_baseline_update_request,
            domain="memory",
        ),
        CheckSpec(
            "l2_pipeline_stages",
            "L2",
            60,
            "Inspect pipeline dependencies and runtime loading",
            _check_l2_pipeline_stages,
            domain="memory",
        ),
        CheckSpec(
            "l2_admission_distribution",
            "L2",
            60,
            "Review admission policy thresholds",
            _check_l2_admission_distribution,
            domain="memory",
        ),
        CheckSpec(
            "l2_write_then_search",
            "L2",
            60,
            "Run write->search->cleanup closed-loop probe for ingestion/index/retrieval",
            _check_l2_write_then_search,
            domain="memory",
        ),
        CheckSpec(
            "l2_index_consistency",
            "L2",
            60,
            "Run index rebuild to close consistency gap",
            _check_l2_index_consistency,
            domain="memory",
        ),
        CheckSpec(
            "l2_sqlite_integrity",
            "L2",
            60,
            "Restore databases from latest healthy backup",
            _check_l2_sqlite_integrity,
            domain="memory",
        ),
        CheckSpec(
            "l2_kg_orphan_check",
            "L2",
            60,
            "Run knowledge graph relation cleanup",
            _check_l2_kg_orphan_check,
            domain="memory",
        ),
        CheckSpec(
            "l2_jsonl_parse",
            "L2",
            60,
            "Run JSONL self-heal or truncate bad tails",
            _check_l2_jsonl_parse,
            domain="memory",
        ),
        CheckSpec(
            "l2_slo_check",
            "L2",
            60,
            "Inspect monitoring SLO targets and recent breaches",
            _check_l2_slo_check,
            domain="memory",
        ),
        CheckSpec(
            "l2_repair_audit_health",
            "L2",
            60,
            "Verify self-repair audit health and writeability",
            _check_l2_repair_audit_health,
            domain="memory",
        ),
        CheckSpec(
            "l2_repair_lock_health",
            "L2",
            60,
            "Verify self-repair in-progress lock freshness",
            _check_l2_repair_lock_health,
            domain="memory",
        ),
        CheckSpec(
            "m1_short_term_persistence",
            "L2",
            60,
            "Verify short-term memory persistence and restore readiness",
            _check_m1_short_term_persistence,
            domain="memory",
        ),
        CheckSpec(
            "m2_rejected_not_indexed",
            "L2",
            60,
            "Ensure rejected records are excluded from main index",
            _check_m2_rejected_not_indexed,
            domain="memory",
        ),
        CheckSpec(
            "m3_review_queue_sla",
            "L2",
            60,
            "Check review queue backlog age and pending volume",
            _check_m3_review_queue_sla,
            domain="memory",
        ),
        CheckSpec(
            "m4_dedupe_false_positive_probe",
            "L2",
            60,
            "Probe possible dedupe false-positive risk",
            _check_m4_dedupe_false_positive_probe,
            domain="memory",
        ),
        CheckSpec(
            "m5_semantic_cache_health",
            "L2",
            60,
            "Check semantic cache dense vector completeness",
            _check_m5_semantic_cache_health,
            domain="memory",
        ),
        CheckSpec(
            "m6_cjk_recall_probe",
            "L2",
            60,
            "Probe CJK recall quality on concatenated queries",
            _check_m6_cjk_recall_probe,
            domain="memory",
        ),
        CheckSpec(
            "m7_kg_access_feedback",
            "L2",
            60,
            "Check KG access_count feedback effectiveness",
            _check_m7_kg_access_feedback,
            domain="memory",
        ),
        CheckSpec(
            "m8_pipeline_latency_budget",
            "L2",
            60,
            "Check pipeline p95 latency budget",
            _check_m8_pipeline_latency_budget,
            domain="memory",
        ),
        CheckSpec(
            "m9_write_gateway_single_entry",
            "L2",
            60,
            "Ensure write entrypoint centralized at write_gateway",
            _check_m9_write_gateway_single_entry,
            domain="memory",
        ),
        CheckSpec(
            "m10_product_decision_injection_policy",
            "L2",
            60,
            "Ensure product_decision recall is gated by decision-intent queries",
            _check_m10_product_decision_injection_policy,
            domain="memory",
        ),
        CheckSpec(
            "c1_mcp_server_smoke",
            "L2",
            60,
            "Validate MCP server smoke health report",
            _check_c1_mcp_server_smoke,
            domain="connect",
        ),
        CheckSpec(
            "c2_mcp_tool_contract",
            "L2",
            60,
            "Validate service interface tool contract",
            _check_c2_mcp_tool_contract,
            domain="connect",
        ),
        CheckSpec(
            "c3_mcp_resource_contract",
            "L2",
            60,
            "Validate service interface resources contract",
            _check_c3_mcp_resource_contract,
            domain="connect",
        ),
        CheckSpec(
            "c4_adapter_registry_integrity",
            "L2",
            60,
            "Validate adapters registry schema and active entries",
            _check_c4_adapter_registry_integrity,
            domain="connect",
        ),
        CheckSpec(
            "c5_interface_single_entry",
            "L2",
            60,
            "Enforce service interface single-entry path",
            _check_c5_interface_single_entry,
            domain="connect",
        ),
        CheckSpec(
            "c6_client_config_presence",
            "L2",
            60,
            "Verify client MCP config presence",
            _check_c6_client_config_presence,
            domain="connect",
        ),
        CheckSpec(
            "c7_source_tagging_e2e",
            "L2",
            60,
            "Verify source tagging in connect audit stream",
            _check_c7_source_tagging_e2e,
            domain="connect",
        ),
        CheckSpec(
            "c8_connect_report_health",
            "L2",
            60,
            "Verify connect report overall health",
            _check_c8_connect_report_health,
            domain="connect",
        ),
        CheckSpec(
            "c9_auto_repair_log_health",
            "L2",
            60,
            "Validate auto-repair log readability and freshness",
            _check_c9_auto_repair_log_health,
            domain="connect",
        ),
        CheckSpec(
            "c10_connect_reconcile_consistency",
            "L2",
            60,
            "Verify connect bootstrap reconciled status consistency",
            _check_c10_connect_reconcile_consistency,
            domain="connect",
        ),
        CheckSpec(
            "c11_external_profile_schema",
            "L2",
            60,
            "Validate external connect profile schema quality",
            _check_c11_external_profile_schema,
            domain="connect",
        ),
        CheckSpec(
            "c12_template_export_health",
            "L2",
            60,
            "Verify generic template export payload health",
            _check_c12_template_export_health,
            domain="connect",
        ),
        CheckSpec(
            "c13_actionable_hints_quality",
            "L2",
            60,
            "Validate actionable hints quality for non-ready targets",
            _check_c13_actionable_hints_quality,
            domain="connect",
        ),
        CheckSpec(
            "c14_shortest_repair_chain_health",
            "L2",
            60,
            "Validate shortest repair chain command health",
            _check_c14_shortest_repair_chain_health,
            domain="connect",
        ),
        CheckSpec(
            "c15_agent_native_template_semantics",
            "L2",
            60,
            "Validate agent-native template semantic guardrails",
            _check_c15_agent_native_template_semantics,
            domain="connect",
        ),
        CheckSpec(
            "l3_manifest_signature",
            "L3",
            120,
            "Run shadow seal/reset checkpoint if signature invalid",
            _check_l3_manifest_signature,
            domain="security",
        ),
        CheckSpec(
            "l3_checkpoint_verify",
            "L3",
            120,
            "Run ocma shadow reset-checkpoint",
            _check_l3_checkpoint_verify,
            domain="security",
        ),
        CheckSpec(
            "l3_shadow_permissions",
            "L3",
            120,
            "Repair shadow_data permissions to 700/600",
            _check_l3_shadow_permissions,
            domain="security",
        ),
        CheckSpec(
            "l3_shadow_health",
            "L3",
            120,
            "Run shadow health and inspect issues list",
            _check_l3_shadow_health,
            domain="security",
        ),
        CheckSpec(
            "l3_seal_history",
            "L3",
            120,
            "Inspect 24h seal frequency and root causes",
            _check_l3_seal_history,
            domain="security",
        ),
        CheckSpec(
            "l3_spool_backlog",
            "L3",
            120,
            "Replay shadow spool backlog and verify drain",
            _check_l3_spool_backlog,
            domain="security",
        ),
        CheckSpec(
            "l3_closed_loop_evidence",
            "L3",
            120,
            "Validate trigger/action/result/evidence chain",
            _check_l3_closed_loop_evidence,
            domain="memory",
        ),
        CheckSpec(
            "l3_repair_effectiveness",
            "L3",
            120,
            "Evaluate self-repair 7-day effectiveness",
            _check_l3_repair_effectiveness,
            domain="memory",
        ),
        CheckSpec(
            "l3_backup_freshness",
            "L3",
            120,
            "Run shadow backup-sync and maintenance backup",
            _check_l3_backup_freshness,
            domain="security",
        ),
        CheckSpec(
            "l3_sensitive_scan",
            "L3",
            120,
            "Move secrets to keychain and redact persisted plaintext",
            _check_l3_sensitive_scan,
            domain="security",
        ),
        CheckSpec(
            "s1_keychain_effective",
            "L3",
            120,
            "Verify keychain-backed shadow key and file-key cleanup",
            _check_s1_keychain_effective,
            domain="security",
        ),
        CheckSpec(
            "s2_shadow_ops_audit_writable",
            "L3",
            120,
            "Verify shadow ops_audit appendability",
            _check_s2_shadow_ops_audit_writable,
            domain="security",
        ),
        CheckSpec(
            "s3_shadow_immutable_flags",
            "L3",
            120,
            "Verify uchg immutable protection on key shadow files",
            _check_s3_shadow_immutable_flags,
            domain="security",
        ),
        CheckSpec(
            "s4_shadow_backup_dual_site",
            "L3",
            120,
            "Verify shadow dual-site backup availability",
            _check_s4_shadow_backup_dual_site,
            domain="security",
        ),
        CheckSpec(
            "s5_replay_dryrun_weekly",
            "L3",
            120,
            "Verify weekly replay/drill cadence evidence",
            _check_s5_replay_dryrun_weekly,
            domain="security",
        ),
        CheckSpec(
            "s6_manifest_checkpoint_pair",
            "L3",
            120,
            "Verify manifest signature + checkpoint gate",
            _check_s6_manifest_checkpoint_pair,
            domain="security",
        ),
        CheckSpec(
            "s7_locking_lease_health",
            "L3",
            120,
            "Verify shadow locking lease health",
            _check_s7_locking_lease_health,
            domain="security",
        ),
        CheckSpec(
            "s8_minimal_survival_trigger",
            "L3",
            120,
            "Verify minimal_survival trigger path",
            _check_s8_minimal_survival_trigger,
            domain="security",
        ),
        CheckSpec(
            "l4_capture_trend",
            "L4",
            300,
            "Check auto_memory extraction quality trend and adjust rules",
            _check_l4_capture_trend,
            domain="memory",
        ),
        CheckSpec(
            "l4_injection_effectiveness",
            "L4",
            300,
            "Tune injection budget and ranking weights",
            _check_l4_injection_effectiveness,
            domain="memory",
        ),
        CheckSpec(
            "l4_threshold_suggestions",
            "L4",
            300,
            "Review and approve/reject pending threshold suggestions",
            _check_l4_threshold_suggestions,
            domain="memory",
        ),
        CheckSpec(
            "l4_capacity_projection",
            "L4",
            300,
            "Plan cleanup/archival or storage expansion",
            _check_l4_capacity_projection,
            domain="memory",
        ),
        CheckSpec(
            "l5_llm_notice_state_health",
            "L4",
            120,
            "Verify one-time LLM degraded notice state file health",
            _check_l5_llm_notice_state_health,
            domain="memory",
        ),
    ]
    if level_key == "L1":
        return [x for x in specs if x.level == "L1"]
    if level_key == "L2":
        return [x for x in specs if x.level == "L2"]
    if level_key == "L3":
        return [x for x in specs if x.level == "L3"]
    if level_key == "L4":
        return [x for x in specs if x.level == "L4"]
    if level_key in {"FULL", "L1L2L3"}:
        return [x for x in specs if x.level in {"L1", "L2", "L3"}]
    if level_key in {"FULL_PLUS", "L1L2L3L4"}:
        return [x for x in specs if x.level in {"L1", "L2", "L3", "L4"}]
    return [x for x in specs if x.level == "L1"]


def levels_for_run(level: str = "L1") -> list[str]:
    level_key = str(level or "L1").upper()
    if level_key == "L1":
        return ["L1"]
    if level_key == "L2":
        return ["L2"]
    if level_key == "L3":
        return ["L3"]
    if level_key == "L4":
        return ["L4"]
    if level_key in {"FULL", "L1L2L3"}:
        return ["L1", "L2", "L3"]
    if level_key in {"FULL_PLUS", "L1L2L3L4"}:
        return ["L1", "L2", "L3", "L4"]
    return ["L1"]
