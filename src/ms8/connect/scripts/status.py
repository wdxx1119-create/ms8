from __future__ import annotations

import json
from pathlib import Path

from ..mcp_server.memory_service_interface import MemoryServiceInterface
from .client_config import selected_targets, target_discovery, target_paths
from .common import connect_package_root, connect_root, load_yaml
from .verify_client_configs import run as verify_client_configs


def run_status() -> dict:
    cfg = load_yaml(connect_package_root() / "config" / "mcp_config.yaml")
    svc = MemoryServiceInterface.from_config(cfg)
    return svc.status()


def _tail_steps(path: Path, limit: int = 10) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8", errors="ignore").splitlines()[-max(1, limit) :]


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _summarize_health(out: dict) -> None:
    health = out.get("health", {})
    if not isinstance(health, dict):
        return
    summary: dict[str, object] = {}
    for key in (
        "timestamp",
        "enabled",
        "memory_injection_events",
        "auto_memory_entries",
        "rates",
        "rates_v2",
        "slo",
        "slo_v2_preview",
        "self_check_stats",
        "shadow_runtime_stats",
        "compression_freshness",
    ):
        if key in health:
            summary[key] = health.get(key)
    alerts_recent = health.get("alerts_recent", [])
    if isinstance(alerts_recent, list):
        summary["alerts_recent_count"] = len(alerts_recent)
        latest_codes: list[str] = []
        latest_items: list[dict[str, object]] = []
        for row in alerts_recent[-3:]:
            if not isinstance(row, dict):
                continue
            code = str(row.get("code", "")).strip()
            if code:
                latest_codes.append(code)
            latest_items.append(
                {
                    "timestamp": str(row.get("timestamp", "")),
                    "code": code,
                    "severity": str(row.get("severity", "")),
                }
            )
        summary["alerts_recent_latest_codes"] = latest_codes
        summary["alerts_recent_latest"] = latest_items
    health_layers: dict[str, object] = {}
    for key in (
        "runtime_health",
        "memory_quality_health",
        "retrieval_safety_health",
        "security_integrity_health",
        "lifecycle_maintenance_health",
        "overall",
        "overall_reasons",
    ):
        if key in health:
            health_layers[key] = health.get(key)
    if health_layers:
        summary["health_layers"] = health_layers
    out["health"] = summary


def _target_connectivity_status(target: str) -> dict:
    out: dict[str, dict] = {}
    discovery = target_discovery(target)
    for name in selected_targets(target):
        path = target_paths(name)[name]
        out[name] = {
            "config_path": str(path),
            "exists": path.exists(),
            "discovery": discovery.get(name, {}),
            "negotiation": {
                "target": name,
                "degrade_mode": "targeted_profile",
                "fallback": "manual_apply_or_verify",
            },
        }
    return out


def _runtime_reports(target: str) -> dict:
    root = connect_root()
    runtime_dir = root / "runtime"
    bootstrap_report = _load_json(runtime_dir / "bootstrap_report.json")
    connect_report = _load_json(runtime_dir / "connect_report.json")
    verify_result = verify_client_configs(target=target)
    connect_result = connect_report.get("result", {}) if isinstance(connect_report.get("result", {}), dict) else {}
    connect_flow_overall_ok = bool(connect_result.get("overall_ok", False))
    current_verify_ok = bool(verify_result.get("ok", False))
    bootstrap_ok = bool(bootstrap_report.get("ok", False)) if bootstrap_report else False
    recovered = (not bootstrap_ok) and current_verify_ok
    hint = str(bootstrap_report.get("hint", "")) if bootstrap_report else ""
    if recovered:
        hint = ""
    return {
        "runtime_dir": str(runtime_dir),
        "bootstrap_report_exists": bool(bootstrap_report),
        "connect_report_exists": bool(connect_report),
        "bootstrap_ok": bool(bootstrap_ok or current_verify_ok),
        "bootstrap_recovered": recovered,
        "connect_flow_overall_ok": connect_flow_overall_ok,
        "current_verify_ok": current_verify_ok,
        "bootstrap_target": str(bootstrap_report.get("target", "")) if bootstrap_report else "",
        "bootstrap_hint": hint,
    }


def main(target: str = "all") -> dict:
    out = run_status()
    _summarize_health(out)
    root = connect_root()
    out["audit_tail"] = _tail_steps(root / "logs" / "audit.log", 8)
    out["target_profiles"] = _target_connectivity_status(target)
    out["target"] = target
    out["runtime_reports"] = _runtime_reports(target)
    return out


if __name__ == "__main__":
    print(main())
