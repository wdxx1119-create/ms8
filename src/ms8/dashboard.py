"""Terminal dashboard for MS8."""

from __future__ import annotations

import shutil
from collections import Counter

from . import __version__
from .runtime import (
    count_memories,
    engine_status,
    ensure_runtime_dirs,
    get_capability_reachability_report,
    get_engine_knowledge_graph_stats,
    get_engine_llm_status,
    get_engine_monitoring_status,
    get_engine_shadow_status,
    get_expression_router_status,
    get_governance_report,
    last_write_time,
    read_memories,
    run_engine_self_check,
)


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


def run_dashboard(limit: int = 5) -> int:
    paths = ensure_runtime_dirs()
    memories = read_memories()
    total = count_memories()
    last = last_write_time() or "N/A"
    source_counts = Counter(str(m.get("source", "unknown")) for m in memories)
    usage = shutil.disk_usage(paths["root"])
    free_gb = usage.free / (1024**3)

    print("MS8 Dashboard\n")
    print(f"Version: {__version__}")
    print(f"Runtime: {paths['root']}")
    overall = "healthy" if paths["memories"].exists() else "warn"
    print(f"Status: {overall}")
    print(f"Memories: {total}")
    print(f"Last write: {last}")
    print(f"Disk free: {free_gb:.1f} GB")
    print(f"Backups: {sum(1 for _ in paths['backups'].glob('*') if _.is_file())}")
    print(f"Logs: {sum(1 for _ in paths['logs'].glob('*') if _.is_file())}\n")
    eng = engine_status()
    print("Engine:")
    print(f" - mode: {eng.get('mode')}")
    print(f" - available: {eng.get('available')}")
    if eng.get("records_file"):
        print(f" - records: {eng.get('records_file')}")
    kg = get_engine_knowledge_graph_stats()
    print(f" - kg entities: {int(kg.get('entity_total', 0) or 0)}")
    print(f" - kg relations: {int(kg.get('relation_total', 0) or 0)}")
    shadow = get_engine_shadow_status()
    print(f" - shadow status: {shadow.get('status', 'unknown')}")
    if "sealed" in shadow:
        print(f" - shadow sealed: {shadow.get('sealed')}")
    llm = get_engine_llm_status()
    print(f" - llm available: {bool(llm.get('available', False))}")
    providers = llm.get("providers", {}) if isinstance(llm, dict) else {}
    if isinstance(providers, dict) and providers:
        rows: list[str] = []
        for name, st in providers.items():
            if not isinstance(st, dict):
                continue
            enabled = bool(st.get("enabled", True))
            ready = bool(st.get("has_api_key", st.get("client_ready", False)))
            rows.append(f"{name}(enabled={enabled}, ready={ready})")
        if rows:
            print(f" - llm providers: {', '.join(rows)}")
    mon = get_engine_monitoring_status()
    print(f" - monitoring enabled: {bool(mon.get('enabled', False))}")
    if isinstance(mon, dict):
        alerts = mon.get("alerts", [])
        if isinstance(alerts, list):
            print(f" - monitoring alerts: {len(alerts)}")
        freshness = mon.get("compression_freshness", {})
        if isinstance(freshness, dict):
            hrs = freshness.get("hours_since_last")
            if isinstance(hrs, (int, float)):
                print(f" - compression freshness(h): {hrs:.1f}")
    expr = get_expression_router_status()
    if isinstance(expr, dict):
        mode_counts = expr.get("mode_counts", {})
        if not isinstance(mode_counts, dict):
            mode_counts = {}
        print(
            " - expression-router: "
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
            print(f" - expression-router reasons: {', '.join(str(x) for x in top_reasons[:3])}")
        print(
            " - expression-router state: "
            f"round={expr.get('current_round', 0)} "
            f"last_mode={expr.get('last_mode', None)} "
            f"profile_evidence={expr.get('profile_evidence_count', 0)}"
        )
    reach = get_capability_reachability_report(top_unreachable=10)
    if isinstance(reach, dict):
        print(
            " - capability-reachability: "
            f"ratio={reach.get('reachable_ratio', 0.0)} "
            f"referenced={reach.get('referenced_methods', 0)}/"
            f"{reach.get('public_methods_total', 0)} "
            f"unreachable={reach.get('unreachable_methods', 0)}"
        )
        top = reach.get("unreachable_top", [])
        if isinstance(top, list) and top:
            print(f" - capability-reachability top-unreachable: {', '.join(str(x) for x in top[:5])}")
    self_check = run_engine_self_check(level="L4")
    if isinstance(self_check, dict):
        if str(self_check.get("status", "")).lower() in {"fail", "error"}:
            overall = "degraded"
        rows = self_check.get("results", [])
        if isinstance(rows, list):
            pass_n = sum(1 for r in rows if isinstance(r, dict) and str(r.get("status", "")).lower() == "pass")
            warn_n = sum(1 for r in rows if isinstance(r, dict) and str(r.get("status", "")).lower() == "warn")
            fail_n = sum(1 for r in rows if isinstance(r, dict) and str(r.get("status", "")).lower() == "fail")
            print(f" - self-check L4: pass={pass_n}, warn={warn_n}, fail={fail_n}")
            warn_ids: list[str] = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                if str(row.get("status", "")).lower() != "warn":
                    continue
                warn_ids.append(str(row.get("check_id", "")))
            if warn_ids:
                print(f" - self-check warn checks: {', '.join(warn_ids[:5])}")
            if "l4_capture_trend" in warn_ids:
                print(
                    "   note: capture trend warn is explainable when recent window has only "
                    "noise/policy drops and no quality samples."
                )
    gov = get_governance_report()
    print(
        " - governance: "
        f"noncanonical={gov.get('noncanonical_records', 0)} "
        f"schema_invalid={gov.get('schema_invalid_count', 0)} "
        f"fallback_write={gov.get('fallback_write_count', 0)} "
        f"fallback_total={gov.get('fallback_total_count', 0)} "
        f"dup_groups={gov.get('duplicate_groups', 0)} "
        f"pending_review={gov.get('pending_review', 0)}"
        f"(oldest_h={gov.get('pending_review_oldest_hours', 0)}) "
        f"revoked={gov.get('revoked', 0)} "
        f"superseded={gov.get('superseded', 0)}"
    )
    top_codes = gov.get("fallback_error_code_top", [])
    if isinstance(top_codes, list) and top_codes:
        print(f" - fallback top error codes: {', '.join(str(x) for x in top_codes[:3])}")
    if bool(gov.get("baseline_update_pending", False)):
        overall = "degraded"
        print(" - governance alert: baseline_update_request pending")
    policy_attack = gov.get("policy_attack_samples", {}) if isinstance(gov.get("policy_attack_samples", {}), dict) else {}
    if policy_attack:
        pa_present = bool(policy_attack.get("present", False))
        pa_ok = bool(policy_attack.get("ok", False))
        pa_failed = int(policy_attack.get("failed_cases", 0) or 0)
        pa_total = int(policy_attack.get("total_cases", 0) or 0)
        pa_age = policy_attack.get("age_hours", None)
        age_text = f"{float(pa_age):.1f}h" if isinstance(pa_age, (int, float)) else "n/a"
        print(
            " - policy-attack-samples: "
            f"present={pa_present} ok={pa_ok} failed={pa_failed}/{pa_total} age={age_text}"
        )
        if pa_present and (not pa_ok or pa_failed > 0):
            print(" - policy-attack-samples alert: closed policy regression suspected")
    trend = gov.get("trend", {})
    if isinstance(trend, dict):
        t24 = trend.get("window_24h", {})
        t7 = trend.get("window_7d", {})
        if isinstance(t24, dict) and isinstance(t7, dict):
            risk24 = str(t24.get("risk", "green"))
            risk7 = str(t7.get("risk", "green"))
            print(
                " - governance trend: "
                f"24h_samples={t24.get('samples', 0)} "
                f"{_format_trend_delta(t24)} "
                f"7d_samples={t7.get('samples', 0)} "
                f"{_format_trend_delta(t7)}"
            )
            if "red" in {risk24, risk7}:
                overall = "degraded"
                print(" - governance trend alert: risk red (overall degraded)")
            elif "yellow" in {risk24, risk7}:
                print(" - governance trend alert: risk yellow")
    print(f" - overall: {overall}")
    print("")

    print("Sources:")
    if source_counts:
        for src, cnt in source_counts.most_common(5):
            print(f" - {src}: {cnt}")
    else:
        print(" - no data")

    print("\nRecent memories:")
    for idx, m in enumerate(memories[-limit:][::-1], start=1):
        text = str(m.get("text", "")).replace("\n", " ")[:80]
        print(f" {idx}. [{m.get('source', 'unknown')}] {text}")
    if not memories:
        print(" - no memories yet")

    return 0
