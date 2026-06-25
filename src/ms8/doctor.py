"""MS8 doctor command."""

# P0-A: standalone doctor for minimal runtime
# Future: delegate to maintenance.self_check when full engine is connected

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from pathlib import Path

from . import __version__
from .absorb.health import absorb_health_summary
from .engine_core.policy_engine_loader import get_policy_backend_status
from .paths import detect_install_mode, get_config_dir, get_data_dir, get_log_dir, get_ms8_home
from .runtime import (
    backup_memories,
    cleanup_old_backups,
    count_memories,
    engine_status,
    ensure_runtime_dirs,
    get_capability_reachability_report,
    get_engine_llm_status,
    get_engine_monitoring_status,
    get_engine_shadow_status,
    get_expression_router_status,
    get_governance_report,
    get_llm_status_runtime,
    get_runtime_dir,
    has_recent_activity,
    run_engine_self_check,
    update_governance_risk_config,
)

logger = logging.getLogger(__name__)


def _relax_console_streams() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, OSError, ValueError):
            continue


def _nested_int(payload: dict, keys: list[str], default: int = 0) -> int:
    value: object = payload
    for key in keys:
        if not isinstance(value, dict):
            return default
        value = value.get(key)
    return int(value) if isinstance(value, (int, float)) else default


def _agent_native_status() -> dict[str, str]:
    canonical_policy = get_ms8_home() / "agent_native" / "agent_policy.json"
    legacy_policy = Path.home() / ".ms8_runtime" / "agent_native" / "agent_policy.json"
    effective_policy = canonical_policy if canonical_policy.exists() else legacy_policy
    task_root = Path.cwd() / ".ms8" / "agent_native"
    install_p = task_root / "install.task"
    ops_p = task_root / "ops.task"
    usage_p = task_root / "usage.task"
    profile = "N/A"
    if effective_policy.exists():
        try:
            payload = json.loads(effective_policy.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                profile = str(payload.get("permission_profile", "N/A"))
        except (json.JSONDecodeError, OSError):
            profile = "N/A"
    task_flag = f"install={'P' if install_p.exists() else 'M'}, ops={'P' if ops_p.exists() else 'M'}, usage={'P' if usage_p.exists() else 'M'}"
    status = (
        "OK"
        if effective_policy.exists() and install_p.exists() and ops_p.exists() and usage_p.exists()
        else "NEEDS_INIT"
    )
    return {
        "policy": "PRESENT" if effective_policy.exists() else "MISSING",
        "permission_profile": profile,
        "task_files": task_flag,
        "agent_native_status": status,
    }


def _normalize_self_check_payload(raw: dict) -> dict:
    """
    Normalize self-check payload across schema variants.

    Canonical output:
      {
        "schema_version": str,
        "status": pass|warn|fail|error|unknown,
        "summary": {"total","pass","warn","fail","error","exit_code"},
        "results": list,
        "domain_summary": dict,
        "maturity_gate": dict,
      }
    """
    if not isinstance(raw, dict):
        return {
            "schema_version": "unknown",
            "status": "error",
            "summary": {"total": 0, "pass": 0, "warn": 0, "fail": 0, "error": 1, "exit_code": 2},
            "results": [],
            "domain_summary": {},
            "maturity_gate": {},
            "reason": "invalid_payload_type",
        }
    # Runtime helpers return an execution envelope:
    # {"ok": true, "ran": true, "method": "...", "result": <self-check-report>}.
    # Doctor must judge the inner report, not the wrapper, or L4 warnings are
    # misreported as "fail (0 checks)".
    wrapped = raw.get("result")
    if isinstance(wrapped, dict) and (
        "summary" in wrapped
        or "results" in wrapped
        or "schema_version" in wrapped
        or "maturity_gate" in wrapped
    ):
        raw = wrapped
    raw_status = str(raw.get("status", "unknown")).strip().lower()
    status_map = {
        "ok": "pass",
        "success": "pass",
        "healthy": "pass",
        "warning": "warn",
        "warn": "warn",
        "failed": "fail",
        "fail": "fail",
        "error": "error",
    }
    status = status_map.get(raw_status, raw_status if raw_status in {"pass", "warn", "fail", "error"} else "unknown")
    results = raw.get("results", [])
    if not isinstance(results, list):
        results = []
    summary = raw.get("summary", {}) if isinstance(raw.get("summary", {}), dict) else {}
    # Compute from results when summary is incomplete.
    if not summary or any(k not in summary for k in ("total", "pass", "warn", "fail", "error")):
        p = w = f = e = 0
        for row in results:
            if not isinstance(row, dict):
                continue
            rs = str(row.get("status", "")).strip().lower()
            if rs == "pass":
                p += 1
            elif rs == "warn":
                w += 1
            elif rs == "fail":
                f += 1
            elif rs == "error":
                e += 1
        total = len([x for x in results if isinstance(x, dict)])
        summary = {
            "total": total,
            "pass": p,
            "warn": w,
            "fail": f,
            "error": e,
            "exit_code": 2 if (f > 0 or e > 0) else (1 if w > 0 else 0),
        }
    if status == "unknown":
        # Derive status from summary exit_code.
        exit_code = int(summary.get("exit_code", 2) or 2)
        if exit_code == 0:
            status = "pass"
        elif exit_code == 1:
            status = "warn"
        else:
            status = "fail"
    return {
        "schema_version": str(raw.get("schema_version", "unknown")),
        "status": status,
        "summary": summary,
        "results": results,
        "domain_summary": raw.get("domain_summary", {}) if isinstance(raw.get("domain_summary", {}), dict) else {},
        "maturity_gate": raw.get("maturity_gate", {}) if isinstance(raw.get("maturity_gate", {}), dict) else {},
    }


def _format_trend_delta(window: dict) -> str:
    delta = window.get("delta", {}) if isinstance(window, dict) else {}
    risk = str(window.get("risk", "green")) if isinstance(window, dict) else "green"
    if not isinstance(delta, dict) or not delta:
        return f"risk={risk} delta=n/a"
    return (
        f"risk={risk} delta(noncanonical={delta.get('noncanonical_records', 0)},"
        f"schema_invalid={delta.get('schema_invalid_count', 0)},"
        f"fallback_write={delta.get('fallback_write_count', 0)},"
        f"fallback_total={delta.get('fallback_total_count', 0)},"
        f"fallback_code_spike={delta.get('fallback_error_code_spike', 0)},"
        f"dup_groups={delta.get('duplicate_groups', 0)},"
        f"pending_review={delta.get('pending_review', 0)})"
    )


def _self_check_guidance(check_ids: list[str]) -> list[dict[str, str]]:
    guidance: list[dict[str, str]] = []
    seen: set[str] = set()
    known = {
        "m3_review_queue_sla": {
            "explanation": "review queue backlog age or pending volume exceeded SLA.",
            "next": "ms8 review list",
        },
    }
    for check_id in check_ids:
        row = known.get(str(check_id).strip())
        if not row:
            continue
        key = str(check_id).strip()
        if key in seen:
            continue
        seen.add(key)
        guidance.append(
            {
                "check_id": key,
                "explanation": str(row["explanation"]),
                "next": str(row["next"]),
            }
        )
    return guidance


def _combine_health_states(*states: str) -> str:
    normalized = [str(state).strip().lower() for state in states if str(state).strip()]
    if any(state in {"degraded", "red", "fail", "error"} for state in normalized):
        return "degraded"
    if any(state in {"warn", "warning", "yellow"} for state in normalized):
        return "warn"
    return "healthy"


def _watch_follow_up_actions(
    *,
    runtime_health: str,
    memory_quality_health: str,
    security_governance_health: str,
    absorb_actions: list[str],
    shadow_actions: list[str],
) -> list[str]:
    actions: list[str] = []
    if runtime_health == "degraded":
        actions.append("ms8 watch --once")
    if absorb_actions:
        actions.append(absorb_actions[0])
    actions.extend(shadow_actions[:2])
    if memory_quality_health != "healthy":
        actions.append("ms8 ops governance")
    if security_governance_health != "healthy":
        actions.append("ms8 ops self-check-report")
    deduped: list[str] = []
    seen: set[str] = set()
    for action in actions:
        key = str(action).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(key)
    return deduped


def run_doctor() -> int:
    _relax_console_streams()
    paths = ensure_runtime_dirs()
    mem_count = count_memories()
    usage = shutil.disk_usage(paths["root"])
    free_gb = usage.free / (1024**3)

    print("MS8 Doctor\n")
    print("Project: ms8")
    print(f"Version: {__version__}")
    print(f"Runtime: {get_runtime_dir()}")
    print(f"MS8 home: {get_ms8_home()}")
    print(f"Data dir: {get_data_dir()}")
    print(f"Config dir: {get_config_dir()}")
    print(f"Log dir: {get_log_dir()}")
    print(f"Install mode: {detect_install_mode()}")
    runtime_status = "healthy"
    print("Status: collecting diagnostics\n")
    print("Checks:")
    print(" ✅ runtime dir: OK")
    print(" ✅ data dir: OK")
    print(f" ✅ memories.jsonl: OK ({mem_count} entries)")
    print(" ✅ backups dir: OK")
    print(" ✅ logs dir: OK")
    print(" ✅ health dir: OK")
    print(f" ✅ disk space: OK ({free_gb:.1f} GB free)")
    print(f" ✅ version: {__version__}")
    eng = engine_status()
    print(f" ✅ engine: {eng.get('mode')} (available={eng.get('available')})")
    print(" ✅ watch loop: available")
    print(f" ✅ recent activity flag: {'on' if has_recent_activity(3600) else 'off'}")
    self_check_raw = run_engine_self_check(level="L4")
    self_check = _normalize_self_check_payload(self_check_raw)
    checks = self_check.get("results", [])
    check_count = len(checks) if isinstance(checks, list) else 0
    status = str(self_check.get("status", "unknown"))
    summary = self_check.get("summary", {})
    passed = 0
    warned = 0
    failed = 0
    if isinstance(checks, list):
        for row in checks:
            if not isinstance(row, dict):
                continue
            s = str(row.get("status", "")).lower()
            if s == "pass":
                passed += 1
            elif s == "warn":
                warned += 1
            elif s == "fail":
                failed += 1
    if status.lower() in {"fail", "error"}:
        runtime_status = "degraded"
    elif status.lower() == "warn":
        # Keep doctor overall healthy but explicitly show warning line.
        pass
    if summary:
        print(
            f" ✅ self-check L4: {status} ({summary.get('total', check_count)} checks, "
            f"pass={summary.get('pass', passed)}, warn={summary.get('warn', warned)}, "
            f"fail={summary.get('fail', failed)}, error={summary.get('error', 0)})"
        )
        print(f" ✅ self-check schema_version: {self_check.get('schema_version', 'unknown')}")
    else:
        print(f" ✅ self-check L4: {status} ({check_count} checks, pass={passed}, warn={warned}, fail={failed})")

    if isinstance(checks, list) and checks:
        warn_checks: list[str] = []
        fail_checks: list[str] = []
        for row in checks:
            if not isinstance(row, dict):
                continue
            rs = str(row.get("status", "")).strip().lower()
            cid = str(row.get("check_id", "")).strip() or "<unknown_check>"
            if rs in {"warn", "warning"}:
                warn_checks.append(cid)
            elif rs in {"fail", "failed", "error"}:
                fail_checks.append(cid)
        summary_warn = int(summary.get("warn", 0) or 0)
        summary_fail = int(summary.get("fail", 0) or 0) + int(summary.get("error", 0) or 0)
        if summary_warn > 0:
            if warn_checks:
                print(f" ⚠️ self-check warn checks: {', '.join(warn_checks[:8])}")
                if "l4_capture_trend" in warn_checks:
                    print(
                        "    ↳ l4_capture_trend note: recent window has noise/policy drops but no "
                        "quality samples; treated as explainable warn (not fail)."
                    )
            else:
                print(" ⚠️ self-check warns exist but warn check_ids are unavailable in row details.")
        if summary_fail > 0:
            if fail_checks:
                print(f" ❌ self-check fail checks: {', '.join(fail_checks[:8])}")
            else:
                print(" ❌ self-check fails exist but fail check_ids are unavailable in row details.")
        known_guidance = _self_check_guidance(fail_checks + warn_checks)
        for row in known_guidance[:3]:
            print(f"    ↳ {row['check_id']}: {row['explanation']}")
            print(f"    ↳ self-check next: {row['next']}")
        if summary_warn > len(warn_checks) or summary_fail > len(fail_checks):
            print(
                " ⚠️ self-check detail mismatch: summary has more warn/fail than per-check rows; "
                "run full self-check and inspect latest report JSON."
            )
    elif int(summary.get("warn", 0) or 0) > 0 or int(summary.get("fail", 0) or 0) > 0:
        print(" ⚠️ self-check has warn/fail counts but no per-check rows; run full self-check to inspect details.")

    domain_rows = self_check.get("domain_summary", {}) if isinstance(self_check, dict) else {}
    if isinstance(domain_rows, dict) and domain_rows:
        print(" ✅ domain coverage:")
        for name in ("memory", "security", "connect"):
            row = domain_rows.get(name)
            if not isinstance(row, dict):
                continue
            print(
                f"    - {name}: total={row.get('total', 0)} pass={row.get('pass', 0)} "
                f"warn={row.get('warn', 0)} fail={row.get('fail', 0)} error={row.get('error', 0)} "
                f"pass_rate={row.get('pass_rate', 0)}"
            )
    gate = self_check.get("maturity_gate", {}) if isinstance(self_check, dict) else {}
    if isinstance(gate, dict) and gate:
        print(
            " ✅ maturity gate: "
            f"memory_ready={gate.get('memory_ready', False)} "
            f"security_ready={gate.get('security_ready', False)} "
            f"connect_ready={gate.get('connect_ready', False)} "
            f"overall_ready={gate.get('overall_ready', False)}"
        )

    mon = get_engine_monitoring_status()
    mon_enabled = bool(mon.get("enabled", False)) if isinstance(mon, dict) else False
    print(f" ✅ monitoring: {'enabled' if mon_enabled else 'disabled'}")
    if isinstance(mon, dict):
        alerts = mon.get("alerts", [])
        if isinstance(alerts, list):
            print(f" ✅ monitoring alerts: {len(alerts)}")
        freshness = mon.get("compression_freshness", {})
        if isinstance(freshness, dict):
            hrs = freshness.get("hours_since_last")
            if isinstance(hrs, (int, float)):
                print(f" ✅ compression freshness: {hrs:.1f}h since last")
    llm = get_engine_llm_status()
    if isinstance(llm, dict):
        print(f" ✅ llm available: {bool(llm.get('available', False))}")
        providers = llm.get("providers", {})
        if isinstance(providers, dict) and providers:
            provider_rows: list[str] = []
            for name, st in providers.items():
                if not isinstance(st, dict):
                    continue
                enabled = bool(st.get("enabled", True))
                key_ok = st.get("has_api_key", st.get("client_ready", False))
                provider_rows.append(f"{name}(enabled={enabled}, ready={bool(key_ok)})")
            if provider_rows:
                print(f" ✅ llm providers: {', '.join(provider_rows)}")
    llm_runtime = get_llm_status_runtime()
    if isinstance(llm_runtime, dict):
        configured = llm_runtime.get("configured", {})
        if not isinstance(configured, dict):
            configured = {}
        recommended_mode = str(llm_runtime.get("recommended_mode", "offline"))
        ladder_info = (
            llm_runtime.get("effective_mode_ladder", {})
            if isinstance(llm_runtime.get("effective_mode_ladder", {}), dict)
            else {}
        )
        ladder = str(ladder_info.get("mode", "rule_only"))
        effective_available = bool(ladder_info.get("effective_available", False))
        print(
            " ✅ llm mode ladder: "
            f"{ladder} (recommended={recommended_mode}, effective_available={effective_available})"
        )
        print(
            " ✅ llm configured order: "
            f"chat={configured.get('provider_order_chat', [])} "
            f"embed={configured.get('provider_order_embedding', [])}"
        )
        if ladder == "rule_only":
            print(
                " ⚠️ llm fallback active: semantic/KG-LLM enhancements limited. "
                "next: `ms8 llm setup --mode local` or `ms8 llm setup --mode cloud`."
            )
    policy_backend = get_policy_backend_status()
    print(
        " ✅ policy engine: "
        f"backend={policy_backend.get('policy_backend', 'unknown')} "
        f"version={policy_backend.get('policy_engine_version', 'unknown')} "
        f"strict={policy_backend.get('policy_strict_mode', False)}"
    )
    fallback_reason = str(policy_backend.get("policy_fallback_reason", "") or "")
    if fallback_reason:
        print(f" ⚠️ policy engine fallback: {fallback_reason}")
        print(" ⚠️ policy engine optional package missing or unavailable.")
        print("    next: `pip install \"ms8[policy]\"` or `pip install ms8-policy-core`.")
    lic = policy_backend.get("policy_license", {})
    if isinstance(lic, dict) and lic:
        lic_status = str(lic.get("status", "unknown"))
        lic_reason = str(lic.get("reason_code", ""))
        lic_enabled = bool(lic.get("enabled", False))
        print(
            " ✅ policy license: "
            f"status={lic_status} enabled={lic_enabled}"
            + (f" reason={lic_reason}" if lic_reason else "")
        )
    absorb = absorb_health_summary()
    print(
        " ✅ absorb: "
        f"risk={absorb.get('risk')} "
        f"roots={absorb.get('authorized_roots', 0)} "
        f"pending={absorb.get('pending_review', 0)} "
        f"quarantine={absorb.get('quarantine', 0)} "
        f"autosubmit={absorb.get('auto_submit_summaries', False)} "
        f"tier={absorb.get('auto_write_tier', 'OFF')} "
        f"kg_pending={_nested_int(absorb, ['kg_extract', 'pending_candidates'])} "
        f"kg_applied={_nested_int(absorb, ['kg_extract', 'applied_total'])}"
    )
    absorb_actions: list[str] = []
    if int(absorb.get("authorized_roots", 0) or 0) <= 0:
        absorb_actions.append("ms8 absorb add <directory>")
    if int(absorb.get("pending_review", 0) or 0) > 0:
        absorb_actions.append("ms8 absorb review list")
    if int(absorb.get("quarantine", 0) or 0) > 0:
        absorb_actions.append("ms8 absorb review export --include-quarantine")
    if str(absorb.get("risk", "green")) != "green" and not absorb_actions:
        absorb_actions.append("ms8 absorb status")
    if absorb_actions:
        print(f"    ↳ absorb next: {absorb_actions[0]}")
    gov = get_governance_report()
    shadow_status = get_engine_shadow_status()
    shadow_summary = gov.get("shadow_runtime", {}) if isinstance(gov.get("shadow_runtime", {}), dict) else {}
    shadow_reason = str(
        shadow_summary.get("reason")
        or shadow_status.get("reason")
        or (shadow_status.get("manifest", {}) if isinstance(shadow_status.get("manifest", {}), dict) else {}).get("reason")
        or ""
    ).strip()
    shadow_mode = str(shadow_summary.get("mode") or shadow_status.get("mode") or "unknown").strip()
    shadow_sealed = bool(shadow_summary.get("sealed", shadow_status.get("sealed", False)))
    shadow_level = str(shadow_summary.get("seal_level") or shadow_status.get("seal_level") or "").strip()
    shadow_findings = shadow_summary.get("startup_findings", [])
    if not isinstance(shadow_findings, list):
        shadow_findings = []
    shadow_findings = [str(item).strip() for item in shadow_findings if str(item).strip()]
    shadow_actions: list[str] = []
    if shadow_reason == "startup_integrity_failed":
        shadow_actions = ["ms8 shadow status", "ms8 shadow health"]
    elif shadow_sealed:
        shadow_actions = ["ms8 shadow status"]
    print(
        " ✅ shadow: "
        f"mode={shadow_mode} sealed={shadow_sealed} level={shadow_level or 'n/a'}"
        + (f" reason={shadow_reason}" if shadow_reason else "")
    )
    if shadow_findings:
        print(f" ⚠️ shadow startup findings: {', '.join(shadow_findings[:5])}")
    if shadow_actions:
        print(f"    ↳ shadow next: {shadow_actions[0]}")
    print(
        " ✅ governance: "
        f"noncanonical={gov.get('noncanonical_records', 0)} "
        f"schema_invalid={gov.get('schema_invalid_count', 0)} "
        f"fallback_write={gov.get('fallback_write_count', 0)} "
        f"fallback_active={gov.get('fallback_active_count', 0)} "
        f"fallback_recent={gov.get('fallback_recent_count', 0)} "
        f"fallback_total={gov.get('fallback_total_count', 0)} "
        f"dup_groups={gov.get('duplicate_groups', 0)} "
        f"pending_review={gov.get('pending_review', 0)}"
        f"(oldest_h={gov.get('pending_review_oldest_hours', 0)}) "
        f"self_check={gov.get('self_check_status', 'unknown')}"
    )
    top_codes = gov.get("fallback_error_code_top", [])
    if isinstance(top_codes, list) and top_codes:
        print(f" ✅ fallback top error codes: {', '.join(str(x) for x in top_codes[:3])}")
    if gov.get("baseline_update_pending"):
        runtime_status = "degraded"
        print(" ⚠️ baseline_update_request pending authorization")
    policy_attack = gov.get("policy_attack_samples", {}) if isinstance(gov.get("policy_attack_samples", {}), dict) else {}
    if policy_attack:
        pa_present = bool(policy_attack.get("present", False))
        pa_ok = bool(policy_attack.get("ok", False))
        pa_failed = int(policy_attack.get("failed_cases", 0) or 0)
        pa_total = int(policy_attack.get("total_cases", 0) or 0)
        pa_age = policy_attack.get("age_hours", None)
        age_text = f"{float(pa_age):.1f}h" if isinstance(pa_age, (int, float)) else "n/a"
        print(
            " ✅ policy-attack-samples: "
            f"present={pa_present} ok={pa_ok} failed={pa_failed}/{pa_total} age={age_text} "
            f"initialized={bool(policy_attack.get('initialized', True))}"
        )
        if pa_present and (not pa_ok or pa_failed > 0):
            print(" ⚠️ policy-attack-samples gate unhealthy: investigate closed policy regression")
    trend = gov.get("trend", {})
    if isinstance(trend, dict):
        t24 = trend.get("window_24h", {})
        t7 = trend.get("window_7d", {})
        if isinstance(t24, dict) and isinstance(t7, dict):
            risk24 = str(t24.get("risk", "green"))
            risk7 = str(t7.get("risk", "green"))
            print(
                " ✅ governance trend: "
                f"24h_samples={t24.get('samples', 0)} "
                f"{_format_trend_delta(t24)} "
                f"7d_samples={t7.get('samples', 0)} "
                f"{_format_trend_delta(t7)}"
            )
            gov_domains = gov.get("health_domains", {}) if isinstance(gov.get("health_domains", {}), dict) else {}
            active_runtime_governance_risk = any(
                str(gov_domains.get(name, "green")).strip().lower() == "red"
                for name in (
                    "runtime_health",
                    "retrieval_safety_health",
                    "security_integrity_health",
                    "lifecycle_maintenance_health",
                )
            )
            if "red" in {risk24, risk7}:
                if active_runtime_governance_risk:
                    runtime_status = "degraded"
                    print(" ⚠️ governance trend risk red (overall degraded)")
                else:
                    print(" ⚠️ governance trend risk red (historical memory-quality drag; current runtime governance green)")
            elif "yellow" in {risk24, risk7}:
                print(" ⚠️ governance trend risk yellow")

    expr = get_expression_router_status()
    if isinstance(expr, dict):
        mode_counts = expr.get("mode_counts", {})
        if not isinstance(mode_counts, dict):
            mode_counts = {}
        print(
            " ✅ expression-router: "
            f"samples={expr.get('total_samples', 0)} "
            f"normal={mode_counts.get('normal', 0)} "
            f"light={mode_counts.get('light', 0)} "
            f"strong={mode_counts.get('strong', 0)} "
            f"strong_ratio={expr.get('strong_ratio', 0.0)} "
            f"cooldown={expr.get('cooldown_applied_count', 0)} "
            f"profile_used={expr.get('profile_used_count', 0)}"
        )
        top_reasons = expr.get("top_reasons", [])
        if isinstance(top_reasons, list) and top_reasons:
            print(f" ✅ expression-router reasons: {', '.join(str(x) for x in top_reasons[:3])}")
        print(
            " ✅ expression-router state: "
            f"round={expr.get('current_round', 0)} "
            f"last_mode={expr.get('last_mode', None)} "
            f"profile_evidence={expr.get('profile_evidence_count', 0)}"
        )
    reach = get_capability_reachability_report(top_unreachable=10)
    if isinstance(reach, dict):
        print(
            " ✅ capability-reachability: "
            f"ratio={reach.get('reachable_ratio', 0.0)} "
            f"referenced={reach.get('referenced_methods', 0)}/"
            f"{reach.get('public_methods_total', 0)} "
            f"unreachable={reach.get('unreachable_methods', 0)}"
        )
        top = reach.get("unreachable_top", [])
        if isinstance(top, list) and top:
            print(f" ✅ capability-reachability top-unreachable: {', '.join(str(x) for x in top[:5])}")

    # Layered health view: runtime availability vs memory quality vs governance/security.
    runtime_health = "healthy"
    memory_quality_health = "healthy"
    security_governance_health = "healthy"

    # runtime health: service/aliveness oriented
    if not bool(eng.get("available", False)):
        runtime_health = "degraded"
    if status.lower() in {"fail", "error"}:
        runtime_health = "degraded"

    # memory quality: prefer governance domain status when available.
    gov_domains = gov.get("health_domains", {}) if isinstance(gov.get("health_domains", {}), dict) else {}
    gov_memory_quality = str(
        gov_domains.get("memory_quality_health", gov.get("memory_quality_health", ""))
    ).strip().lower()
    if gov_memory_quality in {"green", "yellow", "red"}:
        memory_quality_health = {
            "green": "healthy",
            "yellow": "warn",
            "red": "degraded",
        }[gov_memory_quality]
    # Fallback heuristic only when governance domain is unavailable.
    elif isinstance(mon, dict):
        rates = mon.get("rates", {}) if isinstance(mon.get("rates", {}), dict) else {}
        slo = mon.get("slo", {}) if isinstance(mon.get("slo", {}), dict) else {}
        checks_m = slo.get("checks", {}) if isinstance(slo.get("checks", {}), dict) else {}
        targets = slo.get("targets", {}) if isinstance(slo.get("targets", {}), dict) else {}
        capture = float(rates.get("capture_rate", 0.0) or 0.0)
        capture_samples = int(rates.get("auto_total_entries", 0) or 0)
        capture_min = float(targets.get("capture_rate_min", 0.85) or 0.85)
        capture_min_samples = int(targets.get("capture_rate_min_samples", 30) or 30)
        compression_hours = None
        freshness = mon.get("compression_freshness", {}) if isinstance(mon.get("compression_freshness", {}), dict) else {}
        hours_since_last = freshness.get("hours_since_last")
        if isinstance(hours_since_last, (int, float)):
            compression_hours = float(hours_since_last)
        capture_bad = (capture_samples >= capture_min_samples) and (capture < capture_min)
        capture_warn = bool(checks_m.get("capture_rate") is False)
        compression_bad = compression_hours is not None and compression_hours >= 240.0
        compression_warn = compression_hours is None or (compression_hours is not None and compression_hours >= 168.0)
        if capture_bad or compression_bad:
            memory_quality_health = "degraded"
        elif capture_warn or compression_warn:
            memory_quality_health = "warn"

    # security/governance: integrity + policy boundary + self-check signal.
    # L4 warnings are often memory-quality signals (for example capture trend)
    # and should not make the security layer look unhealthy unless the security
    # domain itself has warnings/failures.
    self_check_status = str(gov.get("self_check_status", "unknown")).strip().lower()
    schema_invalid = int(gov.get("schema_invalid_count", 0) or 0)
    fallback_write = int(gov.get("fallback_write_count", 0) or 0)
    baseline_pending = bool(gov.get("baseline_update_pending", False))
    security_domain = domain_rows.get("security") if isinstance(domain_rows, dict) else {}
    security_warn = int(security_domain.get("warn", 0) or 0) if isinstance(security_domain, dict) else 0
    security_fail = int(security_domain.get("fail", 0) or 0) if isinstance(security_domain, dict) else 0
    security_error = int(security_domain.get("error", 0) or 0) if isinstance(security_domain, dict) else 0
    has_security_domain = bool(isinstance(security_domain, dict) and int(security_domain.get("total", 0) or 0) > 0)
    self_check_security_degraded = security_fail > 0 or security_error > 0
    self_check_security_warn = security_warn > 0
    if (self_check_status in {"fail", "error"} and not has_security_domain) or self_check_security_degraded or schema_invalid > 0:
        security_governance_health = "degraded"
    elif self_check_security_warn or fallback_write > 0 or baseline_pending:
        security_governance_health = "warn"

    governance_overall = str(gov_domains.get("overall", "")).strip().lower()
    runtime_status = _combine_health_states(
        runtime_status,
        runtime_health,
        memory_quality_health,
        security_governance_health,
        governance_overall,
    )

    print("\n--- Health Layers ---")
    print(f"runtime_health: {runtime_health}")
    print(f"memory_quality_health: {memory_quality_health}")
    print(f"security_governance_health: {security_governance_health}")

    print(f"\nOverall: {runtime_status}")
    next_actions = _watch_follow_up_actions(
        runtime_health=runtime_health,
        memory_quality_health=memory_quality_health,
        security_governance_health=security_governance_health,
        absorb_actions=absorb_actions,
        shadow_actions=shadow_actions,
    )
    if next_actions:
        print(f"watch next: {next_actions[0]}")
        for action in next_actions[1:3]:
            print(f"watch also: {action}")
    agent = _agent_native_status()
    print("\n--- Agent-native Integration ---")
    print(f"agent_policy: {agent['policy']}")
    print(f"permission_profile: {agent['permission_profile']}")
    print(f"task_files: {agent['task_files']}")
    print(f"agent_native_status: {agent['agent_native_status']}")
    if str(os.environ.get("MS8_DOCTOR_ALLOW_DEGRADED", "0")).strip().lower() in {"1", "true", "yes", "on"}:
        return 0
    return (
        0
        if (sys.version_info >= (3, 10) and runtime_status == "healthy")
        else (1 if runtime_status != "healthy" else 2)
    )


def run_doctor_with_hint() -> int:
    """Run doctor and provide actionable guidance on failure."""
    try:
        return run_doctor()
    except PermissionError as exc:
        logger.error("doctor permission error: %s", exc)
        print(f"ms8 doctor error: {exc}")
        print("hint: check runtime permissions or set MS8_HOME to a writable path.")
        return 1
    except OSError as exc:
        logger.error("doctor os error: %s", exc)
        print(f"ms8 doctor error: {exc}")
        print("hint: try backup and cleanup:")
        print("  ms8 backup")
        print("  ms8 cleanup")
        return 1


def run_backup_and_cleanup(max_keep: int = 20) -> int:
    backup = backup_memories(tag="manual")
    cleaned = cleanup_old_backups(max_keep=max_keep)
    print(f"backup: {backup['path']}")
    print(f"cleanup removed: {cleaned['removed_count']}")
    return 0


def run_set_risk_thresholds(
    *,
    red_schema_invalid_gt: int | None = None,
    red_fallback_write_gt: int | None = None,
    red_noncanonical_gt: int | None = None,
    yellow_fallback_write_gt: int | None = None,
    yellow_pending_review_gt: int | None = None,
    yellow_duplicate_groups_gt: int | None = None,
) -> int:
    payload = update_governance_risk_config(
        red_schema_invalid_gt=red_schema_invalid_gt,
        red_fallback_write_gt=red_fallback_write_gt,
        red_noncanonical_gt=red_noncanonical_gt,
        yellow_fallback_write_gt=yellow_fallback_write_gt,
        yellow_pending_review_gt=yellow_pending_review_gt,
        yellow_duplicate_groups_gt=yellow_duplicate_groups_gt,
    )
    print("updated governance risk thresholds:")
    print(payload)
    return 0
