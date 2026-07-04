"""MS8 CLI."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import __version__
from .absorb.cli import run_absorb_cli
from .absorb.project_memory.cli import run_project_memory_cli
from .agent_native import run_agent_cli
from .ask import run_ask
from .dashboard import run_dashboard
from .demo import run_demo
from .doctor import run_backup_and_cleanup, run_doctor_with_hint, run_set_risk_thresholds
from .lifecycle import clean_runtime, render_lifecycle_result, reset_runtime, uninstall_runtime
from .onboarding import onboarding_status, run_onboarding
from .runtime import (
    advanced_insight_status_runtime,
    backfill_auto_memory_ids_runtime,
    cleanup_old_memory_runtime,
    configure_llm_mode_runtime,
    engine_status,
    export_support_bundle_runtime,
    feedback_record_runtime,
    generate_synthetic_candidates_runtime,
    get_augmented_context_runtime,
    get_background_subagent_task_runtime,
    get_context_with_blocks_runtime,
    get_engine_llm_status,
    get_github_skill_catalog_runtime,
    get_governance_report,
    get_llm_guide_runtime,
    get_llm_status_runtime,
    git_commit_runtime,
    git_history_runtime,
    graph_extract_runtime,
    graph_health_runtime,
    graph_list_relations_runtime,
    graph_maint_runtime,
    graph_neighbors_runtime,
    graph_path_runtime,
    graph_repair_access_runtime,
    graph_search_entities_runtime,
    graph_stats_runtime,
    graph_timeline_runtime,
    install_all_built_in_skills_runtime,
    install_built_in_skill_runtime,
    install_skill_from_file_runtime,
    install_skill_from_github_search_runtime,
    install_skill_from_registry_runtime,
    install_skill_runtime,
    is_git_available_runtime,
    is_learning_enabled_runtime,
    learn_skill_runtime,
    list_archived_logs_runtime,
    list_skills_runtime,
    list_subagent_tasks_runtime,
    list_subagents_runtime,
    load_skill_with_tool_runtime,
    meta_cognition_status_runtime,
    monitoring_status_runtime,
    prepare_graph_offline_cleanup_runtime,
    preview_rollback_auto_approved_synthetic,
    preview_weekly_compression_runtime,
    purge_test_memory_data_runtime,
    rebalance_feedback_distribution_runtime,
    refresh_skill_index_runtime,
    repair_duplicates_after_compression,
    restore_short_term_by_topic_runtime,
    retry_background_subagent_task_runtime,
    review_batch_runtime,
    review_list_runtime,
    review_relabel_runtime,
    rollback_auto_approved_synthetic,
    run_learning_tasks_runtime,
    run_meta_cognition_runtime,
    run_validation_suite_runtime,
    run_weekly_compression,
    search_skills_runtime,
    security_disable_runtime,
    security_enable_runtime,
    security_lock_runtime,
    security_recover_runtime,
    security_status_runtime,
    security_unlock_runtime,
    self_check_report_runtime,
    self_repair_history_runtime,
    self_repair_report_runtime,
    self_repair_rollback_runtime,
    self_repair_run_runtime,
    shadow_archive_spool_runtime,
    shadow_health_runtime,
    shadow_recover_runtime,
    shadow_seal_runtime,
    shadow_status_runtime,
    shadow_unseal_runtime,
    skill_categories_runtime,
    skill_github_search_runtime,
    skill_index_stats_runtime,
    skill_suggest_runtime,
    skill_tags_runtime,
    skill_updates_runtime,
    spawn_subagent_runtime,
    synthetic_confirm_runtime,
    synthetic_health_runtime,
    synthetic_list_runtime,
    synthetic_rebalance_runtime,
    synthetic_reject_runtime,
    synthetic_review_runtime,
    threshold_approve_runtime,
    threshold_list_runtime,
    threshold_reject_runtime,
    uninstall_skill_runtime,
)
from .service import (
    absorb_service_status,
    install_absorb_service,
    install_service,
    remove_absorb_service,
    remove_service,
    service_status,
)
from .shortcut import ensure_shortcuts_once, install_shortcuts, remove_shortcuts, shortcut_status
from .watch import run_watch

logger = logging.getLogger(__name__)


_LABS_EXPERIMENTAL_OPS_CMDS = {
    "synthetic-generate",
    "advanced-insight-status",
    "meta-status",
    "meta-run",
    "context-blocks",
    "augmented-context",
    "github-catalog",
    "subagent-spawn",
    "subagent-retry",
    "skill-install-search",
    "skill-index-refresh",
}

_LABS_MIGRATION_HINTS = {
    "synthetic": "ms8 labs synthetic <subcommand>",
    "synthetic-generate": "ms8 labs synthetic generate",
    "advanced-insight-status": "ms8 labs insight status",
    "meta-status": "ms8 labs meta status",
    "meta-run": "ms8 labs meta run",
    "context-blocks": "ms8 labs context blocks",
    "augmented-context": "ms8 labs context augment",
    "github-catalog": "ms8 labs github catalog",
    "subagent-spawn": "ms8 labs subagents spawn",
    "subagent-retry": "ms8 labs subagents retry",
    "skill-install-search": "ms8 labs skills install-search",
    "skill-index-refresh": "ms8 labs skills index-refresh",
}


def _read_labs_enabled() -> bool:
    from .runtime import ensure_runtime_dirs

    paths = ensure_runtime_dirs()
    cfg_file = paths["config_file"]
    try:
        payload = json.loads(cfg_file.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return False
        labs = payload.get("labs", {})
        if isinstance(labs, dict):
            return bool(labs.get("enabled", False))
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        logger.debug("Failed to read labs enabled flag: %s", exc)
        return False
    return False


def _write_labs_enabled(enabled: bool) -> dict:
    from .runtime import ensure_runtime_dirs

    paths = ensure_runtime_dirs()
    cfg_file = paths["config_file"]
    payload: dict = {}
    try:
        if cfg_file.exists():
            loaded = json.loads(cfg_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = loaded
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        logger.debug("Failed to load existing config.json before labs flag write: %s", exc)
        payload = {}
    labs = payload.get("labs", {})
    if not isinstance(labs, dict):
        labs = {}
    labs["enabled"] = bool(enabled)
    payload["labs"] = labs
    cfg_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "success", "enabled": bool(enabled), "config_file": str(cfg_file)}


def _emit_usage_error(message: str) -> int:
    logger.debug("usage_error: %s", message)
    print(message, file=sys.stderr)
    return 2


def _labs_gate_error(cmd_key: str) -> int:
    hint = _LABS_MIGRATION_HINTS.get(cmd_key, "")
    msg = (
        f"ms8 {cmd_key}: labs command disabled by default; run `ms8 labs enable` first"
        if cmd_key == "synthetic"
        else f"ms8 ops {cmd_key}: labs command disabled by default; run `ms8 labs enable` first"
    )
    if hint:
        msg += f"\nmigration hint: this is an experimental command path (`{hint}`)."
    msg += "\nuse `ms8 labs status` to check gate state."
    return _emit_usage_error(msg)


def _run_labs_command(args: argparse.Namespace) -> int:
    if args.labs_cmd == "status":
        print(
            json.dumps(
                {
                    "status": "success",
                    "labs_enabled": _read_labs_enabled(),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.labs_cmd == "enable":
        print(json.dumps(_write_labs_enabled(True), ensure_ascii=False, indent=2))
        return 0
    if args.labs_cmd == "disable":
        print(json.dumps(_write_labs_enabled(False), ensure_ascii=False, indent=2))
        return 0
    if not _read_labs_enabled():
        key_map = {
            "synthetic": "synthetic",
            "meta": "meta-run",
            "insight": "advanced-insight-status",
            "context": "augmented-context" if getattr(args, "labs_context_cmd", "") == "augment" else "context-blocks",
            "subagents": "subagent-spawn" if getattr(args, "labs_subagents_cmd", "") == "spawn" else "subagent-retry",
            "github": "github-catalog",
            "skills": "skill-install-search" if getattr(args, "labs_skills_cmd", "") == "install-search" else "skill-index-refresh",
        }
        return _labs_gate_error(key_map.get(str(args.labs_cmd), str(args.labs_cmd)))
    if args.labs_cmd == "synthetic":
        if args.labs_synthetic_cmd == "generate":
            out = generate_synthetic_candidates_runtime(limit=args.limit)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0 if bool(out.get("ok", False)) else 1
        if args.labs_synthetic_cmd == "rollback-auto":
            since = max(1, int(args.since_hours))
            out = (
                preview_rollback_auto_approved_synthetic(since_hours=since)
                if bool(args.preview)
                else rollback_auto_approved_synthetic(since_hours=since)
            )
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0 if str(out.get("status", "")).lower() not in {"error", "failed"} else 1
        if args.labs_synthetic_cmd == "list":
            out = synthetic_list_runtime(status=args.status, limit=args.limit)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0 if bool(out.get("ok", False)) else 1
        if args.labs_synthetic_cmd == "confirm":
            ids = [x.strip() for x in str(args.ids or "").split(",") if x.strip()] or None
            out = synthetic_confirm_runtime(candidate_ids=ids, min_score=args.min_score)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0 if bool(out.get("ok", False)) else 1
        if args.labs_synthetic_cmd == "reject":
            ids = [x.strip() for x in str(args.ids or "").split(",") if x.strip()]
            out = synthetic_reject_runtime(candidate_ids=ids)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0 if bool(out.get("ok", False)) else 1
        if args.labs_synthetic_cmd == "review":
            raw = Path(args.file).read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, list):
                return _emit_usage_error("ms8 labs synthetic review: --file must contain a JSON list")
            out = synthetic_review_runtime(decisions=data)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0 if bool(out.get("ok", False)) else 1
        if args.labs_synthetic_cmd == "health":
            out = synthetic_health_runtime()
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0 if bool(out.get("ok", False)) else 1
        if args.labs_synthetic_cmd == "rebalance":
            out = synthetic_rebalance_runtime(
                max_auto_accept=args.max_auto_accept,
                apply_writeback=bool(args.apply_writeback),
            )
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0 if bool(out.get("ok", False)) else 1
        return _emit_usage_error(
            "ms8 labs synthetic: choose generate|rollback-auto|list|confirm|reject|review|health|rebalance"
        )
    if args.labs_cmd == "meta":
        if args.labs_meta_cmd == "status":
            out = meta_cognition_status_runtime()
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0 if bool(out.get("ok", False)) else 1
        if args.labs_meta_cmd == "run":
            out = run_meta_cognition_runtime(period=args.period)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0 if bool(out.get("ok", False)) else 1
        return _emit_usage_error("ms8 labs meta: choose status|run")
    if args.labs_cmd == "insight":
        if args.labs_insight_cmd == "status":
            out = advanced_insight_status_runtime()
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0 if bool(out.get("ok", False)) else 1
        return _emit_usage_error("ms8 labs insight: choose status")
    if args.labs_cmd == "context":
        if args.labs_context_cmd == "blocks":
            out = get_context_with_blocks_runtime()
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0 if bool(out.get("ok", False)) else 1
        if args.labs_context_cmd == "augment":
            out = get_augmented_context_runtime(
                message=args.message,
                include_blocks=not bool(args.no_blocks),
                graph_limit=args.graph_limit,
            )
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0 if bool(out.get("ok", False)) else 1
        return _emit_usage_error("ms8 labs context: choose blocks|augment")
    if args.labs_cmd == "subagents":
        if args.labs_subagents_cmd == "spawn":
            out = spawn_subagent_runtime(
                subagent_name=args.name,
                task=args.task,
                background=bool(args.background),
            )
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0 if bool(out.get("ok", False)) else 1
        if args.labs_subagents_cmd == "retry":
            out = retry_background_subagent_task_runtime(task_id=args.task_id)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0 if bool(out.get("ok", False)) else 1
        return _emit_usage_error("ms8 labs subagents: choose spawn|retry")
    if args.labs_cmd == "github":
        if args.labs_github_cmd == "catalog":
            out = get_github_skill_catalog_runtime(org=args.org)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0 if bool(out.get("ok", False)) else 1
        return _emit_usage_error("ms8 labs github: choose catalog")
    if args.labs_cmd == "skills":
        if args.labs_skills_cmd == "install-search":
            out = install_skill_from_github_search_runtime(skill_name=args.name, repository=args.repository)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0 if bool(out.get("ok", False)) else 1
        if args.labs_skills_cmd == "index-refresh":
            out = refresh_skill_index_runtime()
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0 if bool(out.get("ok", False)) else 1
        return _emit_usage_error("ms8 labs skills: choose install-search|index-refresh")
    return _emit_usage_error("ms8 labs: choose status|enable|disable|synthetic|meta|insight|context|subagents|github|skills")


def _print_connect_bootstrap_summary(marker: dict | None) -> None:
    payload = marker if isinstance(marker, dict) else {}
    connect_bootstrap = payload.get("connect_bootstrap", {}) if isinstance(payload.get("connect_bootstrap", {}), dict) else {}
    print("[ms8] first-run setup completed.")
    if connect_bootstrap.get("skipped", False):
        reason = str(connect_bootstrap.get("reason", "disabled"))
        print(f"[ms8] auto-connect skipped: {reason}")
        print("[ms8] quick guide: ms8 connect guide --mode both")
        return
    ok = bool(connect_bootstrap.get("ok", False))
    print(f"[ms8] auto-connect: {'ok' if ok else 'degraded'}")
    if not ok:
        hint = str(connect_bootstrap.get("hint", "") or "").strip()
        if hint:
            print(f"[ms8] hint: {hint}")
    first_install = (
        connect_bootstrap.get("first_install_report", {})
        if isinstance(connect_bootstrap.get("first_install_report", {}), dict)
        else {}
    )
    if first_install:
        txt_path = str(first_install.get("text_path", "") or "").strip()
        json_path = str(first_install.get("json_path", "") or "").strip()
        if txt_path:
            print(f"[ms8] connect report: {txt_path}")
        if json_path:
            try:
                payload = json.loads(Path(json_path).read_text(encoding="utf-8"))
                hints = payload.get("actionable_hints", []) if isinstance(payload.get("actionable_hints", []), list) else []
                if hints:
                    print("[ms8] connect actionable hints:")
                    for item in hints[:3]:
                        if isinstance(item, str) and item.strip():
                            print(f"[ms8] - {item}")
                    if len(hints) > 3:
                        print(f"[ms8] ... and {len(hints) - 3} more hints in connect report.")
                profiles = payload.get("profiles", {}) if isinstance(payload.get("profiles", {}), dict) else {}
                if profiles:
                    degraded = sorted(
                        [k for k, v in profiles.items() if isinstance(v, dict) and str(v.get("status", "")) == "degraded"]
                    )
                    manual = sorted(
                        [k for k, v in profiles.items() if isinstance(v, dict) and str(v.get("status", "")) == "manual"]
                    )
                    if degraded or manual:
                        chain_targets = degraded[:2] + manual[:1]
                        chain_cmd_parts = [f"ms8 connect apply --target {t}" for t in chain_targets] + [
                            f"ms8 connect verify --target {t}" for t in chain_targets
                        ]
                        short_chain = " && ".join(chain_cmd_parts)
                        print("[ms8] shortest repair chain:")
                        print(f"[ms8] {short_chain}")
            except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
                logger.debug("Failed to parse first-install JSON connect report: %s", exc)
    try:
        from .connect.scripts.common import connect_root

        report_path = connect_root() / "runtime" / "connect_report.json"
        if report_path.exists():
            report = json.loads(report_path.read_text(encoding="utf-8"))
            readiness = report.get("target_readiness", {}) if isinstance(report.get("target_readiness", {}), dict) else {}
            profiles = readiness.get("profiles", {}) if isinstance(readiness.get("profiles", {}), dict) else {}
            if profiles:
                ready = sorted([k for k, v in profiles.items() if isinstance(v, dict) and v.get("status") == "ready"])
                degraded = sorted([k for k, v in profiles.items() if isinstance(v, dict) and v.get("status") == "degraded"])
                manual = sorted([k for k, v in profiles.items() if isinstance(v, dict) and v.get("status") == "manual"])
                if ready:
                    print(f"[ms8] ready: {', '.join(ready)}")
                if degraded:
                    print(f"[ms8] degraded: {', '.join(degraded)}")
                if manual:
                    print(f"[ms8] manual: {', '.join(manual)}")
                    if "codex" in manual:
                        print(
                            "[ms8] codex hint: run `ms8 connect apply --target codex` then "
                            "`ms8 connect verify --target codex`."
                        )
                    if "claude_code" in manual:
                        print(
                            "[ms8] claude_code hint: run `ms8 connect apply --target claude_code` then "
                            "`ms8 connect verify --target claude_code`."
                        )
    except (ImportError, OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.debug("Failed to summarize connect runtime readiness: %s", exc)
    print("[ms8] connect quick-start:")
    print("[ms8] 1) ms8 connect bootstrap --target all")
    print("[ms8] 2) ms8 connect verify --target all")
    print("[ms8] 3) ms8 connect guide --mode both")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ms8", description="MS8 local memory system")
    parser.add_argument("--verbose", action="store_true", help="enable verbose output")

    sub = parser.add_subparsers(dest="command")

    sub.add_parser("version", help="show version")
    p_engine = sub.add_parser("engine", help="engine status and mode")
    p_engine_sub = p_engine.add_subparsers(dest="engine_cmd")
    p_engine_status = p_engine_sub.add_parser("status", help="show active engine status")
    p_engine_status.add_argument("--format", choices=["default", "text"], default="default")

    p_demo = sub.add_parser("demo", help="run demo")
    p_demo.add_argument("--scenario", default="default", help="demo scenario")

    p_doctor = sub.add_parser("doctor", help="run health checks")
    p_doctor.add_argument("--set-risk", action="store_true", help="update governance risk thresholds and exit")
    p_doctor.add_argument("--red-schema-invalid-gt", type=int, default=None)
    p_doctor.add_argument("--red-fallback-write-gt", type=int, default=None)
    p_doctor.add_argument("--red-noncanonical-gt", type=int, default=None)
    p_doctor.add_argument("--yellow-fallback-write-gt", type=int, default=None)
    p_doctor.add_argument("--yellow-pending-review-gt", type=int, default=None)
    p_doctor.add_argument("--yellow-duplicate-groups-gt", type=int, default=None)
    p_ask = sub.add_parser("ask", help="quick save/search")
    p_ask.add_argument("query", help='search text, or save using "记住 xxx"/"save xxx"')
    p_ask.add_argument("--limit", type=int, default=5, help="max search results")

    p_absorb = sub.add_parser("absorb", help="authorized local document absorption")
    p_absorb_sub = p_absorb.add_subparsers(dest="absorb_cmd")
    p_project_memory = p_absorb_sub.add_parser("project-memory", help="manage local project-memory indexes")
    p_project_memory_sub = p_project_memory.add_subparsers(dest="pm_cmd")
    p_pm_init = p_project_memory_sub.add_parser("init", help="register a project directory")
    p_pm_init.add_argument("project_dir")
    p_pm_init.add_argument("--name", default=None)
    p_project_memory_sub.add_parser("list", help="list registered projects")
    for _pm_cmd in ("scan", "index", "build", "submit", "status", "doctor", "watch", "service-install", "service-remove", "service-status", "enable-auto-write", "disable-auto-write"):
        _pm_parser = p_project_memory_sub.add_parser(_pm_cmd)
        _pm_parser.add_argument("--name", default=None)
        if _pm_cmd == "index":
            _pm_parser.add_argument("--full", action="store_true")
        elif _pm_cmd in {"build", "submit"}:
            _pm_parser.add_argument("--force", action="store_true")
        elif _pm_cmd == "watch":
            _pm_parser.add_argument("--duration", type=float, default=None)
            _pm_parser.add_argument("--no-index", action="store_true")
            _pm_parser.add_argument("--build", action="store_true")
            _pm_parser.add_argument("--submit-summary", action="store_true")
        elif _pm_cmd == "service-install":
            _pm_parser.add_argument("--no-build", action="store_true")
            _pm_parser.add_argument("--no-submit-summary", action="store_true")
            _pm_parser.add_argument("--no-index", action="store_true")
    p_pm_search = p_project_memory_sub.add_parser("search", help="search one registered project")
    p_pm_search.add_argument("query")
    p_pm_search.add_argument("--name", default=None)
    p_pm_search.add_argument("--limit", type=int, default=10)
    p_pm_search.add_argument("--pretty", action="store_true")
    for _pm_cmd in ("service-install-all", "service-remove-all", "service-status-all"):
        _pm_parser = p_project_memory_sub.add_parser(_pm_cmd)
        if _pm_cmd == "service-install-all":
            _pm_parser.add_argument("--no-build", action="store_true")
            _pm_parser.add_argument("--no-submit-summary", action="store_true")
            _pm_parser.add_argument("--no-index", action="store_true")
    p_absorb_add = p_absorb_sub.add_parser("add", help="authorize one local directory")
    p_absorb_add.add_argument("path")
    p_absorb_add.add_argument("--confirm-high-risk", action="store_true", help="allow high-risk roots after confirmation")
    p_absorb_remove = p_absorb_sub.add_parser("remove", help="remove one authorized directory")
    p_absorb_remove.add_argument("path")
    p_absorb_sub.add_parser("list", help="list authorized roots and excludes")
    p_absorb_exclude = p_absorb_sub.add_parser("exclude", help="manage exclude patterns")
    p_absorb_exclude_sub = p_absorb_exclude.add_subparsers(dest="exclude_cmd")
    p_absorb_exclude_add = p_absorb_exclude_sub.add_parser("add", help="add exclude pattern")
    p_absorb_exclude_add.add_argument("pattern")
    p_absorb_sub.add_parser("rescan", help="discover authorized files without parsing them")
    p_absorb_ingest = p_absorb_sub.add_parser("ingest", help="parse and locally index discovered files")
    p_absorb_ingest.add_argument("--limit", type=int, default=100)
    p_absorb_ingest.add_argument("--submit-summaries", action="store_true", help="opt-in document summary submission")
    p_absorb_sub.add_parser("status", help="show absorb status")
    p_absorb_review = p_absorb_sub.add_parser("review", help="manage pending review and quarantine items")
    p_absorb_review_sub = p_absorb_review.add_subparsers(dest="review_cmd")
    p_absorb_review_list = p_absorb_review_sub.add_parser("list", help="list review items")
    p_absorb_review_list.add_argument("--limit", type=int, default=50)
    p_absorb_review_approve = p_absorb_review_sub.add_parser("approve", help="approve one pending chunk")
    p_absorb_review_approve.add_argument("chunk_id")
    p_absorb_review_approve.add_argument("--submit", action="store_true", help="also submit approved summary to MS8")
    p_absorb_review_reject = p_absorb_review_sub.add_parser("reject", help="reject one pending chunk")
    p_absorb_review_reject.add_argument("chunk_id")
    p_absorb_review_reject.add_argument("--reason", default="user_rejected")
    p_absorb_review_restore = p_absorb_review_sub.add_parser("restore", help="restore one rejected chunk to pending review")
    p_absorb_review_restore.add_argument("chunk_id")
    p_absorb_review_submit = p_absorb_review_sub.add_parser("submit", help="submit one local indexed chunk summary to MS8")
    p_absorb_review_submit.add_argument("chunk_id")
    p_absorb_review_approve_all = p_absorb_review_sub.add_parser("approve-all", help="approve pending chunks in bulk; dry-run by default")
    p_absorb_review_approve_all.add_argument("--risk", default="", help="optional risk_level filter")
    p_absorb_review_approve_all.add_argument("--limit", type=int, default=50)
    p_absorb_review_approve_all.add_argument("--submit", action="store_true", help="also submit approved chunks to MS8")
    p_absorb_review_approve_all.add_argument("--apply", action="store_true", help="apply changes; without this only previews")
    p_absorb_review_reject_all = p_absorb_review_sub.add_parser("reject-all", help="reject pending chunks in bulk; dry-run by default")
    p_absorb_review_reject_all.add_argument("--reason", default="bulk_rejected")
    p_absorb_review_reject_all.add_argument("--risk", default="", help="optional risk_level filter")
    p_absorb_review_reject_all.add_argument("--limit", type=int, default=50)
    p_absorb_review_reject_all.add_argument("--apply", action="store_true", help="apply changes; without this only previews")
    p_absorb_review_export = p_absorb_review_sub.add_parser("export", help="export pending review items")
    p_absorb_review_export.add_argument("--limit", type=int, default=100)
    p_absorb_review_export.add_argument("--include-quarantine", action="store_true")
    p_absorb_search = p_absorb_sub.add_parser("search", help="search local absorb index")
    p_absorb_search.add_argument("query")
    p_absorb_search.add_argument("--limit", type=int, default=10)
    p_absorb_search.add_argument("--pretty", action="store_true", help="show a compact human-readable search report")
    p_absorb_auto = p_absorb_sub.add_parser("autosubmit", help="manage low-risk summary auto-submission")
    p_absorb_auto_sub = p_absorb_auto.add_subparsers(dest="autosubmit_cmd")
    p_absorb_auto_sub.add_parser("enable")
    p_absorb_auto_sub.add_parser("disable")
    p_absorb_auto_tier = p_absorb_auto_sub.add_parser("tier", help="set auto-write tier")
    p_absorb_auto_tier.add_argument("tier", choices=["OFF", "SUMMARY_ONLY", "LOW_RISK_CHUNKS", "REVIEWED_ONLY"])
    p_absorb_auto_run = p_absorb_auto_sub.add_parser("run", help="run tiered auto-write preview or apply")
    p_absorb_auto_run.add_argument("--limit", type=int, default=20)
    p_absorb_auto_run.add_argument("--daily-cap", type=int, default=20)
    p_absorb_auto_run.add_argument("--apply", action="store_true", help="apply auto-write; without this only previews")
    p_absorb_auto_rollback = p_absorb_auto_sub.add_parser("rollback", help="rollback recent absorb auto-writes; dry-run by default")
    p_absorb_auto_rollback.add_argument("--since-hours", type=int, default=1)
    p_absorb_auto_rollback.add_argument("--limit", type=int, default=100)
    p_absorb_auto_rollback.add_argument("--apply", action="store_true", help="apply rollback; without this only previews")
    p_absorb_auto_rollback.add_argument("--source-system", default="absorb", help="rollback only records tagged with this source system")
    p_absorb_auto_sub.add_parser("status")
    p_absorb_kg = p_absorb_sub.add_parser("kg-extract", help="preview/apply KG extraction from safe absorb chunks")
    p_absorb_kg.add_argument("--limit", type=int, default=50)
    p_absorb_kg.add_argument("--force", action="store_true", help="force KG re-extraction for already processed chunks")
    p_absorb_kg.add_argument("--apply", action="store_true", help="apply extraction; without this only previews")
    p_absorb_start = p_absorb_sub.add_parser("start", help="watch authorized roots in foreground")
    p_absorb_start.add_argument("--duration", type=float, default=None, help="seconds to run before exiting")
    p_absorb_start.add_argument("--submit-summaries", action="store_true", help="opt-in document summary submission")
    p_absorb_sub.add_parser("stop", help="stop watcher placeholder")

    p_watch = sub.add_parser("watch", help="periodic doctor+backup+cleanup")
    p_watch.add_argument("--interval", type=int, default=1800, help="seconds between checks")
    p_watch.add_argument("--once", action="store_true", help="run one cycle and exit")

    p_service = sub.add_parser("service", help="manage launchd watch service")
    p_service_sub = p_service.add_subparsers(dest="service_cmd")
    s_install = p_service_sub.add_parser("install", help="install and load service")
    s_install.add_argument("--interval", type=int, default=1800, help="watch interval seconds")
    p_service_sub.add_parser("remove", help="unload and remove service")
    p_service_sub.add_parser("status", help="service status")
    p_service_sub.add_parser("absorb-install", help="install and load absorb watcher service")
    p_service_sub.add_parser("absorb-remove", help="unload and remove absorb watcher service")
    p_service_sub.add_parser("absorb-status", help="absorb watcher service status")

    p_backup = sub.add_parser("backup", help="snapshot memories")
    p_backup.add_argument("--max-keep", type=int, default=20, help="keep latest N backups")
    p_cleanup = sub.add_parser("cleanup", help="cleanup old backups")
    p_cleanup.add_argument("--max-keep", type=int, default=20, help="keep latest N backups")
    p_clean = sub.add_parser("clean", help="clean runtime caches/logs (safe)")
    p_clean.add_argument("--dry-run", action="store_true", help="preview only, do not delete")
    p_reset = sub.add_parser("reset", help="reset runtime derived state and keep core memory data")
    p_reset.add_argument("--dry-run", action="store_true", help="preview only, do not delete")
    p_reset.add_argument("--no-backup", action="store_true", help="skip reset backup snapshot")
    p_uninstall = sub.add_parser("uninstall", help="uninstall runtime and optional data purge")
    p_uninstall.add_argument("--dry-run", action="store_true", help="preview only, do not delete")
    p_uninstall.add_argument("--purge-data", action="store_true", help="remove full runtime root including data")
    p_uninstall.add_argument("--no-backup", action="store_true", help="skip uninstall backup snapshot")
    p_uninstall.add_argument(
        "--confirm",
        default="",
        help="required for non-dry-run uninstall: set to UNINSTALL",
    )

    p_dash = sub.add_parser("dashboard", help="show runtime dashboard")
    p_dash.add_argument("--limit", type=int, default=5, help="recent memory rows")

    p_shortcut = sub.add_parser("shortcut", help="manage desktop shortcuts")
    p_shortcut_sub = p_shortcut.add_subparsers(dest="shortcut_cmd")
    p_shortcut_sub.add_parser("install", help="install desktop shortcuts")
    p_shortcut_sub.add_parser("remove", help="remove desktop shortcuts")
    p_shortcut_sub.add_parser("status", help="show desktop shortcuts status")

    p_synth = sub.add_parser("synthetic", help="legacy experimental synthetic commands (prefer `ms8 labs synthetic`)")
    p_synth_sub = p_synth.add_subparsers(dest="synthetic_cmd")
    p_synth_rb = p_synth_sub.add_parser("rollback-auto", help="rollback auto-approved synthetic memories")
    p_synth_rb.add_argument("--since-hours", type=int, default=1, help="rollback time window in hours")
    p_synth_rb.add_argument("--preview", action="store_true", help="preview rollback target only")
    p_synth_list = p_synth_sub.add_parser("list", help="list synthetic candidates")
    p_synth_list.add_argument("--status", default="review", help="candidate status")
    p_synth_list.add_argument("--limit", type=int, default=20, help="max candidates")
    p_synth_confirm = p_synth_sub.add_parser("confirm", help="confirm synthetic candidates")
    p_synth_confirm.add_argument("--ids", default="", help="comma-separated candidate ids")
    p_synth_confirm.add_argument("--min-score", type=float, default=None, help="minimum score")
    p_synth_reject = p_synth_sub.add_parser("reject", help="reject synthetic candidates")
    p_synth_reject.add_argument("--ids", required=True, help="comma-separated candidate ids")
    p_synth_review = p_synth_sub.add_parser("review", help="batch review synthetic candidates from json file")
    p_synth_review.add_argument("--file", required=True, help="json file with decisions list")
    p_synth_sub.add_parser("health", help="show synthetic subsystem health")
    p_synth_rebalance = p_synth_sub.add_parser("rebalance", help="rebalance synthetic review queue")
    p_synth_rebalance.add_argument("--max-auto-accept", type=int, default=40, help="max auto accept count")
    p_synth_rebalance.add_argument("--apply-writeback", action="store_true", help="persist rebalance statuses")

    p_connect = sub.add_parser("connect", help="configure and verify MCP client integration")
    p_connect_sub = p_connect.add_subparsers(dest="connect_cmd")
    p_connect_run = p_connect_sub.add_parser(
        "run",
        help="run full connect flow: detect/install/configure/smoke/verify/apply/report",
    )
    p_connect_run.add_argument(
        "--target",
        default="all",
        help="target profile: all|claude_desktop|claude_code|cursor|windsurf|openclaw|hermes|cline|roo|continue|cherry_studio|codex|generic_json",
    )
    p_connect_bootstrap = p_connect_sub.add_parser(
        "bootstrap",
        help="auto detect/apply/verify/smoke MCP client integration (recommended for install)",
    )
    p_connect_bootstrap.add_argument("--target", default="claude_desktop")
    p_connect_bootstrap.add_argument("--no-auto-fix", action="store_true")
    p_connect_bootstrap.add_argument("--silent", action="store_true")
    p_connect_bootstrap.add_argument("--dry-run", action="store_true")
    p_connect_auto = p_connect_sub.add_parser(
        "auto",
        help="fully automatic connect with self-heal retries (recommended for no-touch onboarding)",
    )
    p_connect_auto.add_argument("--target", default="all")
    p_connect_auto.add_argument("--no-self-heal", action="store_true")
    p_connect_auto.add_argument("--max-retries", type=int, default=3, help="max bootstrap attempts")
    p_connect_auto.add_argument("--dry-run", action="store_true")
    p_connect_gen = p_connect_sub.add_parser("generate", help="generate client config snippets")
    p_connect_gen.add_argument(
        "--target",
        default="all",
        help="target profile: all|claude_desktop|claude_code|cursor|windsurf|openclaw|hermes|cline|roo|continue|cherry_studio|codex|generic_json",
    )
    p_connect_apply = p_connect_sub.add_parser("apply", help="apply generated snippets to configured clients")
    p_connect_apply.add_argument(
        "--target",
        default="all",
        help="target profile: all|claude_desktop|claude_code|cursor|windsurf|openclaw|hermes|cline|roo|continue|cherry_studio|codex|generic_json",
    )
    p_connect_verify = p_connect_sub.add_parser("verify", help="verify client configs and legacy-path migration")
    p_connect_verify.add_argument(
        "--target",
        default="all",
        help="target profile: all|claude_desktop|claude_code|cursor|windsurf|openclaw|hermes|cline|roo|continue|cherry_studio|codex|generic_json",
    )
    p_connect_rollback = p_connect_sub.add_parser("rollback", help="remove client MCP config files")
    p_connect_rollback.add_argument(
        "--target",
        default="all",
        help="target profile: all|claude_desktop|claude_code|cursor|windsurf|openclaw|hermes|cline|roo|continue|cherry_studio|codex|generic_json",
    )
    p_connect_rollback.add_argument("--dry-run", action="store_true", help="preview only, do not modify files")
    p_connect_rollback.add_argument(
        "--force-delete-full-config",
        action="store_true",
        help="dangerous: delete entire target config file instead of removing only ms8-memory entry",
    )
    p_connect_status = p_connect_sub.add_parser("status", help="show connect runtime status")
    p_connect_status.add_argument(
        "--target",
        default="all",
        help="target profile: all|claude_desktop|claude_code|cursor|windsurf|openclaw|hermes|cline|roo|continue|cherry_studio|codex|generic_json",
    )
    p_connect_smoke = p_connect_sub.add_parser("smoke", help="run connect smoke test")
    p_connect_smoke.add_argument(
        "--target",
        default="all",
        help="target profile: all|claude_desktop|claude_code|cursor|windsurf|openclaw|hermes|cline|roo|continue|cherry_studio|codex|generic_json",
    )
    p_connect_list = p_connect_sub.add_parser("list-targets", help="list supported target profiles and resolved paths")
    p_connect_list.add_argument("--compact", action="store_true", help="compact view: target + resolved path + exists")
    p_connect_template = p_connect_sub.add_parser("template", help="show AGENTS template path and quick usage")
    p_connect_template.add_argument("--target", default="generic_json", help="template focus target (display only)")
    p_connect_template.add_argument("--client-name", default="", help="optional unknown client name for custom export")
    p_connect_template.add_argument(
        "--output",
        default="",
        help="optional output path for custom template snippet (defaults to $MS8_HOME/connect/runtime/client_snippets/<client>_mcp.json)",
    )
    p_connect_guide = p_connect_sub.add_parser("guide", help="show connect usage guide")
    p_connect_guide.add_argument("--mode", default="both", choices=["manual", "agent", "both"], help="guide mode")
    p_connect_sub.add_parser("scan", help="scan and register local adapters/tools")
    p_connect_sub.add_parser("install-env", help="prepare connect runtime directories")

    p_agent = sub.add_parser("agent", help="agent-native onboarding and task templates")
    p_agent_sub = p_agent.add_subparsers(dest="agent_cmd")
    p_agent_init = p_agent_sub.add_parser("init", help="initialize agent-native files")
    p_agent_init.add_argument("--profile", default="DEFAULT_SAFE", choices=["DEFAULT_SAFE", "TRUSTED_AGENT"])
    p_agent_init.add_argument("--dry-run", action="store_true", help="preview only")
    p_agent_init.add_argument("--force", action="store_true", help="overwrite task files in place")
    p_agent_init.add_argument("--confirm", action="store_true", help="confirm TRUSTED_AGENT profile actions")
    p_agent_perm = p_agent_sub.add_parser("permission", help="show or upgrade permission profile")
    p_agent_perm_sub = p_agent_perm.add_subparsers(dest="permission_cmd")
    p_agent_perm_up = p_agent_perm_sub.add_parser("upgrade", help="upgrade profile to TRUSTED_AGENT")
    p_agent_perm_up.add_argument("--to", default="TRUSTED_AGENT", choices=["TRUSTED_AGENT"])
    p_agent_perm_up.add_argument("--dry-run", action="store_true", help="preview only")
    p_agent_perm_up.add_argument("--confirm", action="store_true", help="confirm upgrade")
    p_agent_task = p_agent_sub.add_parser("task", help="list/show agent tasks")
    p_agent_task_sub = p_agent_task.add_subparsers(dest="task_cmd")
    p_agent_task_sub.add_parser("list", help="list available task templates")
    p_agent_task_sub.add_parser("verify", help="verify task template version/consistency")
    p_agent_task_show = p_agent_task_sub.add_parser("show", help="show one task template")
    p_agent_task_show.add_argument("name", choices=["install", "ops", "check", "report", "usage", "absorb"])
    p_agent_sub.add_parser("remove", help="archive/remove project-level .ms8/agent_native")
    p_agent_migrate = p_agent_sub.add_parser(
        "migrate-policy-path",
        help="migrate legacy policy path to canonical MS8_HOME/agent_native",
    )
    p_agent_migrate.add_argument("--dry-run", action="store_true", help="preview migration")
    p_agent_migrate.add_argument("--force", action="store_true", help="overwrite canonical policy when exists")
    p_agent_migrate.add_argument(
        "--cleanup-legacy",
        action="store_true",
        help="remove legacy policy file after successful migration",
    )
    p_agent_bug = p_agent_sub.add_parser("bug-report", help="create redacted bug-report bundle")
    p_agent_bug.add_argument("--bundle", action="store_true", default=True, help="kept for compatibility")
    p_agent_bug.add_argument("--redact", action="store_true", default=True, help="generate redacted outputs")
    p_agent_policy = p_agent_sub.add_parser("policy", help="agent policy tools")
    p_agent_policy_sub = p_agent_policy.add_subparsers(dest="policy_cmd")
    p_agent_policy_sub.add_parser("verify", help="verify policy schema and safety gates")
    p_agent_run = p_agent_sub.add_parser("run", help="run agent-native orchestrated flows")
    p_agent_run_sub = p_agent_run.add_subparsers(dest="run_cmd")
    p_agent_run_install = p_agent_run_sub.add_parser("install", help="run install-task orchestration and output report")
    p_agent_run_install.add_argument("--profile", default="DEFAULT_SAFE", choices=["DEFAULT_SAFE", "TRUSTED_AGENT"])
    p_agent_run_install.add_argument("--confirm", action="store_true", help="required with TRUSTED_AGENT")
    p_agent_run_check = p_agent_run_sub.add_parser("check", help="run doctor/status + issue/repair-preview decision")
    p_agent_run_check.add_argument("--no-repair-preview", action="store_true", help="disable dry-run repair preview")
    p_agent_run_report = p_agent_run_sub.add_parser("report", help="run critical detection and bug-report on demand")
    p_agent_run_report.add_argument("--no-redact", action="store_true", help="disable redaction in bug-report bundle")
    p_agent_run_absorb = p_agent_run_sub.add_parser("absorb", help="run safe absorb orchestration and output MS8_AGENT_RESULT")
    p_agent_run_absorb.add_argument("--mode", default="status", choices=["status", "setup", "search", "review"])
    p_agent_run_absorb.add_argument("--path", default="", help="directory to authorize when --mode setup")
    p_agent_run_absorb.add_argument("--query", default="", help="query text when --mode search")
    p_agent_run_absorb.add_argument("--confirm", action="store_true", help="explicitly confirm folder authorization for setup")
    p_agent_run_daily = p_agent_run_sub.add_parser("daily", help="run check then report in one command")
    p_agent_run_daily.add_argument("--no-repair-preview", action="store_true", help="disable dry-run repair preview")
    p_agent_run_daily.add_argument("--no-redact", action="store_true", help="disable redaction in bug-report bundle")
    p_agent_run_daily.add_argument("--verbose-output", action="store_true", help="show full nested payload")

    p_llm = sub.add_parser("llm", help="configure multi-LLM providers and fallback mode")
    p_llm_sub = p_llm.add_subparsers(dest="llm_cmd")
    p_llm_sub.add_parser("status", help="show detected provider readiness and recommendation")
    p_llm_setup = p_llm_sub.add_parser("setup", help="apply provider routing mode into runtime config")
    p_llm_setup.add_argument(
        "--mode",
        default="auto",
        choices=["auto", "local", "hybrid", "cloud"],
        help="routing mode: auto/local/hybrid/cloud",
    )
    p_llm_sub.add_parser("guide", help="show cloud/local configuration guide")

    p_security = sub.add_parser("security", help="manage memory encryption/security")
    p_security_sub = p_security.add_subparsers(dest="security_cmd")
    p_security_sub.add_parser("status", help="show security status")
    p_sec_enable = p_security_sub.add_parser("enable", help="enable encryption")
    p_sec_enable.add_argument("--password", required=True, help="master password")
    p_sec_disable = p_security_sub.add_parser("disable", help="disable encryption")
    p_sec_disable.add_argument("--password", required=True, help="master password")
    p_sec_unlock = p_security_sub.add_parser("unlock", help="unlock encrypted memory")
    p_sec_unlock.add_argument("--password", required=True, help="master password")
    p_security_sub.add_parser("lock", help="lock encrypted memory")
    p_sec_recover = p_security_sub.add_parser("recover", help="recover with recovery key")
    p_sec_recover.add_argument("--recovery-key", required=True, help="recovery key")
    p_sec_recover.add_argument("--new-password", required=True, help="new master password")

    p_shadow = sub.add_parser("shadow", help="manage shadow protection system")
    p_shadow_sub = p_shadow.add_subparsers(dest="shadow_cmd")
    p_shadow_sub.add_parser("status", help="show shadow status")
    p_shadow_sub.add_parser("health", help="run shadow health check")
    p_shadow_seal = p_shadow_sub.add_parser("seal", help="seal memory writes")
    p_shadow_seal.add_argument("--reason", default="manual", help="seal reason")
    p_shadow_seal.add_argument("--level", default="hard", choices=["soft", "hard"], help="seal level")
    p_shadow_unseal = p_shadow_sub.add_parser("unseal", help="unseal memory writes")
    p_shadow_unseal.add_argument("--reason", default="manual", help="unseal reason")
    p_shadow_recover = p_shadow_sub.add_parser("recover", help="recover from shadow events")
    p_shadow_recover.add_argument("--max-events", type=int, default=200, help="max events to replay")
    p_shadow_recover.add_argument("--dry-run", action="store_true", help="preview only, do not perform recovery")
    p_shadow_recover.add_argument(
        "--confirm",
        default="",
        help="required for non-dry-run shadow recover: set to SHADOW_RECOVERY",
    )

    p_skill = sub.add_parser("skill", help="manage memory skill system")
    p_skill_sub = p_skill.add_subparsers(dest="skill_cmd")
    p_skill_sub.add_parser("list", help="list installed skills")
    p_skill_install = p_skill_sub.add_parser("install", help="install skill from GitHub URL")
    p_skill_install.add_argument("--url", required=True, help="GitHub skill URL")
    p_skill_install.add_argument("--scope", default="project", help="scope: project/user/system")
    p_skill_remove = p_skill_sub.add_parser("uninstall", help="uninstall skill")
    p_skill_remove.add_argument("--name", required=True, help="skill name")
    p_skill_remove.add_argument("--scope", default="project", help="scope: project/user/system")
    p_skill_search = p_skill_sub.add_parser("search", help="search local skill index")
    p_skill_search.add_argument("query", help="query text")
    p_skill_search.add_argument("--category", default=None, help="optional category")
    p_skill_search.add_argument("--limit", type=int, default=20, help="max results")
    p_skill_sub.add_parser("updates", help="check installed skill updates")
    p_skill_sub.add_parser("categories", help="list indexed skill categories")
    p_skill_sub.add_parser("tags", help="list indexed skill tags")
    p_skill_suggest = p_skill_sub.add_parser("suggest", help="suggest skills by prefix")
    p_skill_suggest.add_argument("prefix", help="name prefix")
    p_skill_suggest.add_argument("--limit", type=int, default=5, help="max suggestions")
    p_skill_github = p_skill_sub.add_parser("github-search", help="search skills from GitHub marketplace")
    p_skill_github.add_argument("--query", default=None, help="search query")
    p_skill_github.add_argument("--category", default=None, help="filter by category")
    p_skill_github.add_argument("--min-stars", type=int, default=0, help="minimum stars")
    p_skill_github.add_argument("--sort-by", default="stars", help="sort: stars/updated/name")
    p_skill_github.add_argument("--limit", type=int, default=20, help="max results")
    p_skill_sub.add_parser("index-stats", help="show local skill index stats")

    p_graph = sub.add_parser("graph", help="knowledge graph operations")
    p_graph_sub = p_graph.add_subparsers(dest="graph_cmd")
    p_graph_sub.add_parser("stats", help="show knowledge graph stats")
    p_graph_extract = p_graph_sub.add_parser("extract", help="batch extract pending knowledge graph items")
    p_graph_extract.add_argument("--limit", type=int, default=20, help="batch size")
    p_graph_extract.add_argument("--force", action="store_true", help="force re-extract")
    p_graph_sub.add_parser("maintain", help="run knowledge graph maintenance")
    p_graph_repair = p_graph_sub.add_parser("repair-access", help="repair entity access counters from anchors")
    p_graph_repair.add_argument("--min-access", type=int, default=1, help="minimum access count")
    p_graph_search = p_graph_sub.add_parser("search", help="search entities")
    p_graph_search.add_argument("query", help="entity query")
    p_graph_search.add_argument("--entity-type", default=None, help="entity type filter")
    p_graph_search.add_argument("--limit", type=int, default=10, help="max entities")
    p_graph_rel = p_graph_sub.add_parser("relations", help="list relations")
    p_graph_rel.add_argument("--entity-name", default="", help="entity name")
    p_graph_rel.add_argument("--relation-type", default=None, help="relation type")
    p_graph_rel.add_argument("--direction", default="both", help="in|out|both")
    p_graph_rel.add_argument("--limit", type=int, default=10, help="max relations")
    p_graph_nb = p_graph_sub.add_parser("neighbors", help="get entity neighbors")
    p_graph_nb.add_argument("--entity-name", required=True, help="entity name")
    p_graph_nb.add_argument("--depth", type=int, default=2, help="neighbor depth")
    p_graph_nb.add_argument("--relation-type", default=None, help="relation type")
    p_graph_nb.add_argument("--limit", type=int, default=10, help="max neighbors")
    p_graph_path = p_graph_sub.add_parser("path", help="find shortest path")
    p_graph_path.add_argument("--start", required=True, help="start entity")
    p_graph_path.add_argument("--end", required=True, help="end entity")
    p_graph_path.add_argument("--max-depth", type=int, default=3, help="max search depth")
    p_graph_tl = p_graph_sub.add_parser("timeline", help="graph timeline")
    p_graph_tl.add_argument("--days", type=int, default=7, help="days window")
    p_graph_tl.add_argument("--limit", type=int, default=10, help="max rows")
    p_graph_sub.add_parser("health", help="graph health check")

    p_review = sub.add_parser("review", help="review queue and threshold approvals")
    p_review_sub = p_review.add_subparsers(dest="review_cmd")
    p_review_sub.add_parser("list", help="list pending review items")
    p_review_sub.add_parser("status", help="alias of list (compat)")
    p_review_batch = p_review_sub.add_parser("batch", help="run batch review")
    p_review_batch.add_argument("--mode", default="triage_default", help="batch mode")
    p_review_batch.add_argument("--limit", type=int, default=30, help="max items")
    p_review_batch.add_argument("--accept-conf-min", type=float, default=0.62, help="accept confidence min")
    p_review_batch.add_argument("--reject-conf-max", type=float, default=0.20, help="reject confidence max")
    p_review_batch.add_argument("--per-category-limit", type=int, default=6, help="per-category limit")
    p_review_batch.add_argument("--drain-reject-conf-max", type=float, default=0.50, help="drain reject confidence max")
    p_review_relabel = p_review_sub.add_parser("relabel", help="relabel a review item")
    p_review_relabel.add_argument("--memory-id", required=True, help="memory record id")
    p_review_relabel.add_argument("--category", required=True, help="new category")
    p_review_relabel.add_argument("--notes", default="", help="optional notes")
    p_threshold = p_review_sub.add_parser("threshold-list", help="list pending threshold suggestions")
    p_threshold.add_argument("--include-processed", action="store_true", help="include approved/rejected items")
    p_threshold_approve = p_review_sub.add_parser("threshold-approve", help="approve threshold suggestion")
    p_threshold_approve.add_argument("--approval-id", required=True, help="approval id")
    p_threshold_approve.add_argument("--approver", default="cli", help="approver id")
    p_threshold_reject = p_review_sub.add_parser("threshold-reject", help="reject threshold suggestion")
    p_threshold_reject.add_argument("--approval-id", required=True, help="approval id")
    p_threshold_reject.add_argument("--approver", default="cli", help="approver id")
    p_threshold_reject.add_argument("--reason", default="manual_reject", help="reject reason")

    p_ops = sub.add_parser("ops", help="operations: self-check/self-repair workflows")
    p_ops_sub = p_ops.add_subparsers(dest="ops_cmd")
    p_ops_sub.add_parser("self-check-report", help="show latest self-check report")
    p_ops_bundle = p_ops_sub.add_parser("support-bundle", help="export redacted support bundle zip")
    p_ops_bundle.add_argument("--output", default="", help="output zip path")
    p_ops_bundle.add_argument("--no-redact", action="store_true", help="disable redaction (not recommended)")
    p_ops_bundle.add_argument("--dry-run", action="store_true", help="preview only, do not write zip")
    p_ops_repair = p_ops_sub.add_parser("self-repair-run", help="run self-repair dry-run/apply")
    p_ops_repair.add_argument("--mode", default="dry-run", choices=["dry-run", "apply"], help="repair mode")
    p_ops_repair.add_argument("--domain", default="", help="optional domain filter")
    p_ops_repair.add_argument("--check-id", default="", help="optional check id filter")
    p_ops_repair.add_argument("--risk", default="", help="optional risk filter R1/R2/R3")
    p_ops_repair.add_argument("--approve-r3", action="store_true", help="approve R3 actions")
    p_ops_repair.add_argument("--auto", action="store_true", help="auto mode hint")
    p_ops_sub.add_parser("self-repair-report", help="show latest self-repair report")
    p_ops_hist = p_ops_sub.add_parser("self-repair-history", help="show self-repair history")
    p_ops_hist.add_argument("--limit", type=int, default=10, help="history row limit")
    p_ops_rollback = p_ops_sub.add_parser("self-repair-rollback", help="rollback one self-repair operation")
    p_ops_rollback.add_argument("--operation-id", required=True, help="operation id to rollback")
    p_ops_sub.add_parser("dedupe-now", help="run duplicate clustering and supersede duplicates now")
    p_ops_sub.add_parser("llm-status", help="show multi-LLM provider health and fallback status")
    p_ops_weekly = p_ops_sub.add_parser("weekly-compress", help="trigger weekly compression")
    p_ops_weekly.add_argument("--confirm", action="store_true", help="confirm compression execution")
    p_ops_arch = p_ops_sub.add_parser("archived-logs", help="list archived daily logs")
    p_ops_arch.add_argument("--limit", type=int, default=20, help="max rows")
    p_ops_sub.add_parser("subagents", help="list subagents")
    p_ops_tasks = p_ops_sub.add_parser("subagent-tasks", help="list background subagent tasks")
    p_ops_tasks.add_argument("--limit", type=int, default=20, help="max rows")
    p_ops_task = p_ops_sub.add_parser("subagent-task", help="get one background subagent task")
    p_ops_task.add_argument("--task-id", required=True, help="task id")
    p_ops_sub.add_parser("validation-suite", help="run validation suite")
    p_ops_sub.add_parser("backfill-ids", help="backfill missing auto-memory record ids")
    p_ops_sub.add_parser("cleanup-memory", help="cleanup old memory data")
    p_ops_sub.add_parser("monitoring-status", help="show monitoring status")
    p_ops_governance = p_ops_sub.add_parser("governance", help="show governance layered health report")
    p_ops_governance.add_argument(
        "--json",
        action="store_true",
        help="compat flag; output is JSON by default",
    )
    p_ops_sub.add_parser("compression-status", help="show compression lifecycle status")
    p_ops_comp_run = p_ops_sub.add_parser("compression-run", help="run compression lifecycle")
    p_ops_comp_run.add_argument("--dry-run", action="store_true", help="preview compression only")
    p_ops_comp_run.add_argument("--confirm", action="store_true", help="confirm execution when not dry-run")
    p_ops_sub.add_parser("compression-repair", help="repair compression duplicate/superseded links")
    p_ops_sub.add_parser("advanced-insight-status", help="experimental: show advanced insight status (prefer `ms8 labs insight status`)")
    p_ops_sub.add_parser("meta-status", help="experimental: show meta-cognition status (prefer `ms8 labs meta status`)")
    p_ops_meta = p_ops_sub.add_parser("meta-run", help="experimental: run one meta-cognition cycle (prefer `ms8 labs meta run`)")
    p_ops_meta.add_argument("--period", default=None, help="optional period (daily/weekly/monthly)")
    p_ops_sub.add_parser("context-blocks", help="experimental: show context blocks (prefer `ms8 labs context blocks`)")
    p_ops_aug = p_ops_sub.add_parser("augmented-context", help="experimental: build augmented context (prefer `ms8 labs context augment`)")
    p_ops_aug.add_argument("--message", required=True, help="input message")
    p_ops_aug.add_argument("--no-blocks", action="store_true", help="disable memory block prefix")
    p_ops_aug.add_argument("--graph-limit", type=int, default=5, help="graph context limit")
    p_ops_syn_gen = p_ops_sub.add_parser("synthetic-generate", help="experimental: generate synthetic candidates (prefer `ms8 labs synthetic generate`)")
    p_ops_syn_gen.add_argument("--limit", type=int, default=20, help="max candidates")
    p_ops_gh = p_ops_sub.add_parser("github-catalog", help="experimental: fetch GitHub skill catalog (prefer `ms8 labs github catalog`)")
    p_ops_gh.add_argument("--org", default="openclaw", help="GitHub org")
    p_ops_git_hist = p_ops_sub.add_parser("git-history", help="show memory git history")
    p_ops_git_hist.add_argument("--max-count", type=int, default=10, help="max commits")
    p_ops_git_commit = p_ops_sub.add_parser("git-commit", help="commit memory git changes")
    p_ops_git_commit.add_argument("--message", default=None, help="optional commit message")
    p_ops_bi = p_ops_sub.add_parser("built-in-install", help="install one built-in skill")
    p_ops_bi.add_argument("--name", required=True, help="built-in skill name")
    p_ops_sub.add_parser("built-in-install-all", help="install all built-in skills")
    p_ops_sf = p_ops_sub.add_parser("skill-install-file", help="install skill from local file/dir")
    p_ops_sf.add_argument("--path", required=True, help="file or dir path")
    p_ops_sf.add_argument("--scope", default="project", help="project/user/system")
    p_ops_sg = p_ops_sub.add_parser("skill-install-search", help="experimental: install skill from GitHub search (prefer `ms8 labs skills install-search`)")
    p_ops_sg.add_argument("--name", required=True, help="skill name")
    p_ops_sg.add_argument("--repository", default=None, help="optional owner/repo")
    p_ops_sr = p_ops_sub.add_parser("skill-install-registry", help="install skill from registry id")
    p_ops_sr.add_argument("--skill-id", required=True, help="registry skill id, e.g. @org/name")
    p_ops_sr.add_argument("--scope", default="project", help="project/user/system")
    p_ops_sub.add_parser("git-available", help="check git integration availability")
    p_ops_sub.add_parser("learning-enabled", help="check whether learning subsystem is enabled")
    p_ops_learn = p_ops_sub.add_parser("learn-skill", help="learn a skill from trajectory JSON")
    p_ops_learn.add_argument("--skill-name", required=True, help="new skill name")
    p_ops_learn.add_argument("--trajectory-file", required=True, help="JSON file containing conversation trajectory list")
    p_ops_learn.add_argument("--instructions", default=None, help="optional extraction instructions")
    p_ops_load_tool = p_ops_sub.add_parser("skill-load-tool", help="load one skill via tool-style output")
    p_ops_load_tool.add_argument("--name", required=True, help="skill name")
    p_ops_sub.add_parser("weekly-compress-preview", help="preview weekly compression plan")
    p_ops_graph_offline = p_ops_sub.add_parser("graph-offline-cleanup", help="prepare graph offline cleanup snapshot")
    p_ops_graph_offline.add_argument("--limit", type=int, default=500, help="max rows")
    p_ops_sub.add_parser("purge-test-memory", help="purge test memory data")
    p_ops_fb = p_ops_sub.add_parser("feedback-rebalance", help="rebalance feedback distribution")
    p_ops_fb.add_argument("--window", type=int, default=None, help="optional recent window")
    p_ops_sub.add_parser("skill-index-refresh", help="experimental: refresh local skill index (prefer `ms8 labs skills index-refresh`)")
    p_ops_sub.add_parser("learning-run-pending", help="run pending learning scheduled tasks")
    p_ops_sub.add_parser("shadow-archive-spool", help="archive shadow spool events")
    p_ops_restore = p_ops_sub.add_parser("short-term-restore", help="restore short-term items by topic")
    p_ops_restore.add_argument("--query", required=True, help="search query")
    p_ops_restore.add_argument("--limit", type=int, default=20, help="max rows")
    p_ops_retry = p_ops_sub.add_parser("subagent-retry", help="experimental: retry one background subagent task (prefer `ms8 labs subagents retry`)")
    p_ops_retry.add_argument("--task-id", required=True, help="task id")
    p_ops_spawn = p_ops_sub.add_parser("subagent-spawn", help="experimental: spawn a subagent task (prefer `ms8 labs subagents spawn`)")
    p_ops_spawn.add_argument("--name", required=True, help="subagent name")
    p_ops_spawn.add_argument("--task", required=True, help="task description")
    p_ops_spawn.add_argument("--background", action="store_true", help="run in background")

    p_feedback = sub.add_parser("feedback", help="record memory quality feedback")
    p_feedback_sub = p_feedback.add_subparsers(dest="feedback_cmd")
    p_fb_record = p_feedback_sub.add_parser("record", help="record feedback for one memory")
    p_fb_record.add_argument("--memory-id", required=True, help="memory id")
    p_fb_record.add_argument("--category", required=True, help="feedback category")
    p_fb_record.add_argument("--signal", required=True, help="feedback signal")
    p_fb_record.add_argument("--helpful", required=True, choices=["true", "false"], help="whether helpful")
    p_fb_record.add_argument("--note", default="", help="feedback note")
    p_fb_record.add_argument("--source", default="user", help="feedback source")
    p_fb_record.add_argument("--confidence", type=float, default=0.0, help="confidence")

    p_labs = sub.add_parser("labs", help="experimental capabilities gate")
    p_labs_sub = p_labs.add_subparsers(dest="labs_cmd")
    p_labs_sub.add_parser("status", help="show labs gate status")
    p_labs_sub.add_parser("enable", help="enable labs commands")
    p_labs_sub.add_parser("disable", help="disable labs commands")
    p_labs_syn = p_labs_sub.add_parser("synthetic", help="experimental synthetic memory workflows")
    p_labs_syn_sub = p_labs_syn.add_subparsers(dest="labs_synthetic_cmd")
    p_labs_syn_gen = p_labs_syn_sub.add_parser("generate", help="generate synthetic candidates")
    p_labs_syn_gen.add_argument("--limit", type=int, default=20, help="max candidates")
    p_labs_syn_rb = p_labs_syn_sub.add_parser("rollback-auto", help="rollback auto-approved synthetic memories")
    p_labs_syn_rb.add_argument("--since-hours", type=int, default=1, help="rollback time window in hours")
    p_labs_syn_rb.add_argument("--preview", action="store_true", help="preview rollback target only")
    p_labs_syn_list = p_labs_syn_sub.add_parser("list", help="list synthetic candidates")
    p_labs_syn_list.add_argument("--status", default="review", help="candidate status")
    p_labs_syn_list.add_argument("--limit", type=int, default=20, help="max candidates")
    p_labs_syn_confirm = p_labs_syn_sub.add_parser("confirm", help="confirm synthetic candidates")
    p_labs_syn_confirm.add_argument("--ids", default="", help="comma-separated candidate ids")
    p_labs_syn_confirm.add_argument("--min-score", type=float, default=None, help="minimum score")
    p_labs_syn_reject = p_labs_syn_sub.add_parser("reject", help="reject synthetic candidates")
    p_labs_syn_reject.add_argument("--ids", required=True, help="comma-separated candidate ids")
    p_labs_syn_review = p_labs_syn_sub.add_parser("review", help="batch review synthetic candidates from json file")
    p_labs_syn_review.add_argument("--file", required=True, help="json file with decisions list")
    p_labs_syn_sub.add_parser("health", help="show synthetic subsystem health")
    p_labs_syn_rebalance = p_labs_syn_sub.add_parser("rebalance", help="rebalance synthetic review queue")
    p_labs_syn_rebalance.add_argument("--max-auto-accept", type=int, default=40, help="max auto accept count")
    p_labs_syn_rebalance.add_argument("--apply-writeback", action="store_true", help="persist rebalance statuses")
    p_labs_meta = p_labs_sub.add_parser("meta", help="experimental meta-cognition workflows")
    p_labs_meta_sub = p_labs_meta.add_subparsers(dest="labs_meta_cmd")
    p_labs_meta_sub.add_parser("status", help="show meta-cognition status")
    p_labs_meta_run = p_labs_meta_sub.add_parser("run", help="run meta-cognition cycle")
    p_labs_meta_run.add_argument("--period", default="daily", help="period hint")
    p_labs_insight = p_labs_sub.add_parser("insight", help="experimental advanced insight status")
    p_labs_insight_sub = p_labs_insight.add_subparsers(dest="labs_insight_cmd")
    p_labs_insight_sub.add_parser("status", help="show advanced insight status")
    p_labs_ctx = p_labs_sub.add_parser("context", help="experimental context helpers")
    p_labs_ctx_sub = p_labs_ctx.add_subparsers(dest="labs_context_cmd")
    p_labs_ctx_sub.add_parser("blocks", help="show context blocks")
    p_labs_ctx_aug = p_labs_ctx_sub.add_parser("augment", help="show augmented context")
    p_labs_ctx_aug.add_argument("--message", required=True, help="query text")
    p_labs_ctx_aug.add_argument("--no-blocks", action="store_true", help="disable block augmentation")
    p_labs_ctx_aug.add_argument("--graph-limit", type=int, default=8, help="graph relation limit")
    p_labs_subagents = p_labs_sub.add_parser("subagents", help="experimental subagent workflows")
    p_labs_subagents_sub = p_labs_subagents.add_subparsers(dest="labs_subagents_cmd")
    p_labs_sub_spawn = p_labs_subagents_sub.add_parser("spawn", help="spawn a subagent task")
    p_labs_sub_spawn.add_argument("--name", required=True, help="subagent name")
    p_labs_sub_spawn.add_argument("--task", required=True, help="task description")
    p_labs_sub_spawn.add_argument("--background", action="store_true", help="run in background")
    p_labs_sub_retry = p_labs_subagents_sub.add_parser("retry", help="retry one background subagent task")
    p_labs_sub_retry.add_argument("--task-id", required=True, help="task id")
    p_labs_git = p_labs_sub.add_parser("github", help="experimental GitHub skill catalog helpers")
    p_labs_git_sub = p_labs_git.add_subparsers(dest="labs_github_cmd")
    p_labs_git_catalog = p_labs_git_sub.add_parser("catalog", help="fetch GitHub skill catalog")
    p_labs_git_catalog.add_argument("--org", required=True, help="GitHub org name")
    p_labs_skills = p_labs_sub.add_parser("skills", help="experimental skill ingestion helpers")
    p_labs_skills_sub = p_labs_skills.add_subparsers(dest="labs_skills_cmd")
    p_labs_skill_search = p_labs_skills_sub.add_parser("install-search", help="install one skill from GitHub search")
    p_labs_skill_search.add_argument("--name", required=True, help="skill name")
    p_labs_skill_search.add_argument("--repository", default=None, help="optional repository hint")
    p_labs_skills_sub.add_parser("index-refresh", help="refresh local skill index from GitHub")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)
    if not args.verbose:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)

    if args.command is None:
        return run_dashboard()
    try:
        if args.command == "version":
            print(f"ms8 {__version__}")
            return 0
        # First-run onboarding + auto shortcut creation (best-effort, non-blocking).
        if args.command != "shortcut":
            if not onboarding_status().get("done"):
                onboarding_result = run_onboarding()
                suppress_bootstrap_summary = args.command == "agent" and getattr(args, "agent_cmd", "") == "run"
                if not suppress_bootstrap_summary:
                    _print_connect_bootstrap_summary(onboarding_result.get("marker", {}))
            ensure_shortcuts_once()
        if args.command == "engine":
            if args.engine_cmd == "status":
                out = engine_status()
                fmt = str(getattr(args, "format", "") or "")
                if fmt == "text":
                    from .paths import detect_install_mode, get_config_dir, get_data_dir, get_log_dir, get_ms8_home

                    print("MS8_STATUS_REPORT")
                    print("report_version=1")
                    print(f"status={'OK' if bool(out.get('available', False)) else 'FAIL'}")
                    print(f"version={__version__}")
                    print(f"python_version={sys.version.split()[0]}")
                    print(f"install_mode={detect_install_mode()}")
                    print(f"ms8_home={get_ms8_home()}")
                    print(f"data_dir={get_data_dir()}")
                    print(f"config_dir={get_config_dir()}")
                    print(f"log_dir={get_log_dir()}")
                    print(f"mode={out.get('mode')}")
                    print(f"available={out.get('available')}")
                    if out.get("error"):
                        print(f"error={out.get('error')}")
                    if out.get("records_file"):
                        print(f"records_file={out.get('records_file')}")
                else:
                    print(f"mode: {out.get('mode')}")
                    print(f"available: {out.get('available')}")
                    if out.get("error"):
                        print(f"error: {out.get('error')}")
                    if out.get("records_file"):
                        print(f"records_file: {out.get('records_file')}")
                return 0
            return _emit_usage_error("ms8 engine: choose status")
        if args.command == "demo":
            return run_demo(scenario=args.scenario)
        if args.command == "doctor":
            if getattr(args, "set_risk", False):
                return run_set_risk_thresholds(
                    red_schema_invalid_gt=args.red_schema_invalid_gt,
                    red_fallback_write_gt=args.red_fallback_write_gt,
                    red_noncanonical_gt=args.red_noncanonical_gt,
                    yellow_fallback_write_gt=args.yellow_fallback_write_gt,
                    yellow_pending_review_gt=args.yellow_pending_review_gt,
                    yellow_duplicate_groups_gt=args.yellow_duplicate_groups_gt,
                )
            return run_doctor_with_hint()
        if args.command == "ask":
            return run_ask(query=args.query, limit=args.limit)
        if args.command == "absorb":
            if args.absorb_cmd == "project-memory":
                return run_project_memory_cli(args)
            return run_absorb_cli(args)
        if args.command == "labs":
            return _run_labs_command(args)
        if args.command == "watch":
            return run_watch(interval_seconds=args.interval, once=args.once)
        if args.command == "service":
            if args.service_cmd == "install":
                out = install_service(interval_seconds=args.interval)
                print(f"service install: {'ok' if out['ok'] else 'fail'}")
                if out.get("stderr"):
                    print(f"stderr: {out['stderr']}")
                return 0 if out["ok"] else 1
            if args.service_cmd == "remove":
                out = remove_service()
                print(f"service removed: {out['plist']}")
                return 0
            if args.service_cmd == "status":
                out = service_status()
                print(f"installed: {'yes' if out['installed'] else 'no'}")
                print(f"running: {'yes' if out['running'] else 'no'}")
                print(f"plist: {out['plist']}")
                print(f"absorb_installed: {'yes' if out.get('absorb_installed') else 'no'}")
                print(f"absorb_running: {'yes' if out.get('absorb_running') else 'no'}")
                print(f"absorb_plist: {out.get('absorb_plist', '')}")
                oc = out.get("openclaw_services", {})
                if isinstance(oc, dict) and oc:
                    print("openclaw:")
                    for label, ok in oc.items():
                        print(f" - {label}: {'running' if ok else 'stopped'}")
                return 0
            if args.service_cmd == "absorb-install":
                out = install_absorb_service()
                print(f"absorb service install: {'ok' if out['ok'] else 'fail'}")
                if out.get("stderr"):
                    print(f"stderr: {out['stderr']}")
                return 0 if out["ok"] else 1
            if args.service_cmd == "absorb-remove":
                out = remove_absorb_service()
                print(f"absorb service removed: {out['plist']}")
                return 0
            if args.service_cmd == "absorb-status":
                out = absorb_service_status()
                print(f"installed: {'yes' if out['installed'] else 'no'}")
                print(f"running: {'yes' if out['running'] else 'no'}")
                print(f"plist: {out['plist']}")
                return 0
            return _emit_usage_error("ms8 service: choose install|remove|status|absorb-install|absorb-remove|absorb-status")
        if args.command == "backup":
            return run_backup_and_cleanup(max_keep=args.max_keep)
        if args.command == "cleanup":
            return run_backup_and_cleanup(max_keep=args.max_keep)
        if args.command == "clean":
            out = clean_runtime(dry_run=bool(args.dry_run))
            print(render_lifecycle_result(out))
            return 0 if out.get("ok", False) else 1
        if args.command == "reset":
            out = reset_runtime(
                dry_run=bool(args.dry_run),
                backup=not bool(args.no_backup),
            )
            print(render_lifecycle_result(out))
            return 0 if out.get("ok", False) else 1
        if args.command == "uninstall":
            if not args.dry_run and str(args.confirm).strip() != "UNINSTALL":
                return _emit_usage_error("ms8 uninstall: pass --confirm UNINSTALL (or use --dry-run)")
            out = uninstall_runtime(
                dry_run=bool(args.dry_run),
                purge_data=bool(args.purge_data),
                backup=not bool(args.no_backup),
                remove_launchd=True,
            )
            print(render_lifecycle_result(out))
            return 0 if out.get("ok", False) else 1
        if args.command == "dashboard":
            return run_dashboard(limit=args.limit)
        if args.command == "shortcut":
            if args.shortcut_cmd == "install":
                out = install_shortcuts()
                print(f"shortcuts installed: {out['desktop']}")
                for f in out["files"]:
                    print(f" - {f}")
                return 0
            if args.shortcut_cmd == "remove":
                out = remove_shortcuts()
                print(f"shortcuts removed: {len(out['removed'])}")
                for f in out["removed"]:
                    print(f" - {f}")
                return 0
            if args.shortcut_cmd == "status":
                out = shortcut_status()
                print(f"desktop: {out['desktop']}")
                print(f"MS8.command: {'yes' if out['main_exists'] else 'no'}")
                print(f"MS8-Doctor.command: {'yes' if out['doctor_exists'] else 'no'}")
                return 0
            return _emit_usage_error("ms8 shortcut: choose install|remove|status")
        if args.command == "synthetic":
            if not _read_labs_enabled():
                return _labs_gate_error("synthetic")
            if args.synthetic_cmd == "rollback-auto":
                since = max(1, int(args.since_hours))
                if bool(args.preview):
                    out = preview_rollback_auto_approved_synthetic(since_hours=since)
                else:
                    out = rollback_auto_approved_synthetic(since_hours=since)
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if str(out.get("status", "")).lower() not in {"error", "failed"} else 1
            if args.synthetic_cmd == "list":
                out = synthetic_list_runtime(status=args.status, limit=args.limit)
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.synthetic_cmd == "confirm":
                ids = [x.strip() for x in str(args.ids or "").split(",") if x.strip()] or None
                out = synthetic_confirm_runtime(candidate_ids=ids, min_score=args.min_score)
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.synthetic_cmd == "reject":
                ids = [x.strip() for x in str(args.ids or "").split(",") if x.strip()]
                out = synthetic_reject_runtime(candidate_ids=ids)
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.synthetic_cmd == "review":
                raw = Path(args.file).read_text(encoding="utf-8")
                data = json.loads(raw)
                if not isinstance(data, list):
                    return _emit_usage_error("ms8 synthetic review: --file must contain a JSON list")
                out = synthetic_review_runtime(decisions=data)
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.synthetic_cmd == "health":
                out = synthetic_health_runtime()
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.synthetic_cmd == "rebalance":
                out = synthetic_rebalance_runtime(
                    max_auto_accept=args.max_auto_accept,
                    apply_writeback=bool(args.apply_writeback),
                )
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            return _emit_usage_error(
                "ms8 synthetic: choose rollback-auto|list|confirm|reject|review|health|rebalance"
            )
        if args.command == "connect":
            if args.connect_cmd == "run":
                from .connect.scripts.connect import run_connect_flow

                out = run_connect_flow(target=str(getattr(args, "target", "all")))
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("result", {}).get("overall_ok", False)) else 1
            if args.connect_cmd == "bootstrap":
                from .connect.scripts.bootstrap import run_bootstrap

                out = run_bootstrap(
                    target=str(getattr(args, "target", "claude_desktop")),
                    auto_fix=not bool(getattr(args, "no_auto_fix", False)),
                    silent=bool(getattr(args, "silent", False)),
                    dry_run=bool(getattr(args, "dry_run", False)),
                )
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.connect_cmd == "auto":
                from .connect.scripts.bootstrap import run_bootstrap

                target = str(getattr(args, "target", "all"))
                auto_fix = not bool(getattr(args, "no_self_heal", False))
                max_retries = max(1, int(getattr(args, "max_retries", 3) or 3))
                dry_run = bool(getattr(args, "dry_run", False))
                attempts: list[dict] = []
                final: dict = {}
                for i in range(max_retries):
                    out = run_bootstrap(
                        target=target,
                        auto_fix=auto_fix,
                        silent=True,
                        dry_run=dry_run,
                    )
                    attempts.append(
                        {
                            "attempt": i + 1,
                            "ok": bool(out.get("ok", False)),
                            "hint": str(out.get("hint", "")),
                            "self_heal": out.get("self_heal", {}),
                        }
                    )
                    final = out
                    if bool(out.get("ok", False)):
                        break
                result = {
                    "ok": bool(final.get("ok", False)),
                    "target": target,
                    "self_heal": auto_fix,
                    "max_retries": max_retries,
                    "attempt_count": len(attempts),
                    "attempts": attempts,
                    "final": final,
                }
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0 if bool(result.get("ok", False)) else 1
            if args.connect_cmd == "generate":
                from .connect.scripts.generate_client_configs import run as connect_generate

                out = connect_generate(target=str(getattr(args, "target", "all")))
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.connect_cmd == "apply":
                from .connect.scripts.apply_client_configs import run as connect_apply

                out = connect_apply(target=str(getattr(args, "target", "all")))
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.connect_cmd == "verify":
                from .connect.scripts.verify_client_configs import run as connect_verify

                out = connect_verify(target=str(getattr(args, "target", "all")))
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.connect_cmd == "rollback":
                from .connect.scripts.rollback_client_configs import run as connect_rollback

                out = connect_rollback(
                    target=str(getattr(args, "target", "all")),
                    dry_run=bool(getattr(args, "dry_run", False)),
                    force_delete_full_config=bool(getattr(args, "force_delete_full_config", False)),
                )
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.connect_cmd == "status":
                from .connect.scripts.status import main as connect_status

                out = connect_status(target=str(getattr(args, "target", "all")))
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.connect_cmd == "smoke":
                from .connect.scripts.smoke_test import run_smoke_test

                out = run_smoke_test(target=str(getattr(args, "target", "all")))
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.connect_cmd == "scan":
                from .connect.scripts.scan_register import run as connect_scan

                out = connect_scan()
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.connect_cmd == "install-env":
                from .connect.scripts.install_env import run as connect_install_env

                out = connect_install_env()
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.connect_cmd == "list-targets":
                from .connect.scripts.client_config import supported_target_matrix

                matrix = supported_target_matrix()
                if bool(getattr(args, "compact", False)):
                    compact = {}
                    for name, meta in matrix.items():
                        disc = meta.get("discovery", {}) if isinstance(meta.get("discovery", {}), dict) else {}
                        compact[name] = {
                            "resolved": disc.get("resolved", ""),
                            "exists": bool(disc.get("resolved_exists", False)),
                        }
                    out = {"ok": True, "targets": compact, "view": "compact"}
                else:
                    out = {"ok": True, "targets": matrix, "view": "full"}
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0
            if args.connect_cmd == "template":
                target = str(getattr(args, "target", "generic_json"))
                client_name = str(getattr(args, "client_name", "")).strip()
                output = str(getattr(args, "output", "")).strip()
                connect_dir = Path(__file__).resolve().parent / "connect"
                agents_file = connect_dir / "AGENTS.md"
                extra: dict[str, object] = {}
                if client_name:
                    from .connect.scripts.client_config import payload_for_target
                    from .connect.scripts.common import connect_root

                    safe_name = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in client_name.lower())
                    out_path = (
                        Path(output)
                        if output
                        else connect_root() / "runtime" / "client_snippets" / f"{safe_name}_mcp.json"
                    )
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    template_payload = payload_for_target("generic_json")
                    out_path.write_text(json.dumps(template_payload, ensure_ascii=False, indent=2), encoding="utf-8")
                    extra = {
                        "custom_template_generated": True,
                        "client_name": client_name,
                        "output": str(out_path),
                        "import_hint": [
                            f"Import {out_path} into {client_name} MCP config.",
                            f"Then run: ms8 connect verify --target {target if target != 'generic_json' else 'all'}",
                        ],
                    }
                out = {
                    "ok": True,
                    "target": target,
                    "template_file": str(agents_file),
                    "quick_start": [
                        f"ms8 connect generate --target {target}",
                        f"ms8 connect apply --target {target}",
                        f"ms8 connect verify --target {target}",
                    ],
                }
                if extra:
                    out.update(extra)
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0
            if args.connect_cmd == "guide":
                mode = str(getattr(args, "mode", "both"))
                connect_dir = Path(__file__).resolve().parent / "connect"
                guide_file = connect_dir / "CONNECT_GUIDE.md"
                if not guide_file.exists():
                    return _emit_usage_error(f"connect guide file missing: {guide_file}")
                if mode == "manual":
                    out = {
                        "ok": True,
                        "mode": mode,
                        "guide_file": str(guide_file),
                        "steps": [
                            "ms8 connect generate --target generic_json",
                            "导入 snippet 到 MCP 客户端配置",
                            "ms8 connect verify --target <target>",
                            "ms8 connect smoke --target <target>",
                        ],
                    }
                    print(json.dumps(out, ensure_ascii=False, indent=2))
                    return 0
                if mode == "agent":
                    out = {
                        "ok": True,
                        "mode": mode,
                        "guide_file": str(guide_file),
                        "steps": [
                            "ms8 connect bootstrap --target all",
                            "ms8 connect apply --target all",
                            "ms8 connect verify --target all",
                        ],
                    }
                    print(json.dumps(out, ensure_ascii=False, indent=2))
                    return 0
                out = {
                    "ok": True,
                    "mode": mode,
                    "guide_file": str(guide_file),
                    "content": guide_file.read_text(encoding="utf-8"),
                }
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0
            return _emit_usage_error(
                "ms8 connect: choose bootstrap|run|generate|apply|verify|rollback|status|smoke|scan|install-env|list-targets|template|guide"
            )
        if args.command == "agent":
            return run_agent_cli(args)
        if args.command == "llm":
            if args.llm_cmd == "status":
                out = get_llm_status_runtime()
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0
            if args.llm_cmd == "setup":
                out = configure_llm_mode_runtime(mode=args.mode)
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.llm_cmd == "guide":
                out = get_llm_guide_runtime()
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0
            return _emit_usage_error("ms8 llm: choose status|setup|guide")
        if args.command == "security":
            if args.security_cmd == "status":
                print(json.dumps(security_status_runtime(), ensure_ascii=False, indent=2))
                return 0
            if args.security_cmd == "enable":
                out = security_enable_runtime(master_password=args.password)
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.security_cmd == "disable":
                out = security_disable_runtime(master_password=args.password)
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.security_cmd == "unlock":
                out = security_unlock_runtime(master_password=args.password)
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.security_cmd == "lock":
                out = security_lock_runtime()
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.security_cmd == "recover":
                out = security_recover_runtime(recovery_key=args.recovery_key, new_master_password=args.new_password)
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            return _emit_usage_error("ms8 security: choose status|enable|disable|unlock|lock|recover")
        if args.command == "shadow":
            if args.shadow_cmd == "status":
                print(json.dumps(shadow_status_runtime(), ensure_ascii=False, indent=2))
                return 0
            if args.shadow_cmd == "health":
                out = shadow_health_runtime()
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.shadow_cmd == "seal":
                out = shadow_seal_runtime(reason=args.reason, level=args.level)
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.shadow_cmd == "unseal":
                out = shadow_unseal_runtime(reason=args.reason)
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.shadow_cmd == "recover":
                if (not bool(args.dry_run)) and str(args.confirm or "").strip() != "SHADOW_RECOVERY":
                    return _emit_usage_error(
                        "ms8 shadow recover: pass --confirm SHADOW_RECOVERY (or use --dry-run)"
                    )
                out = shadow_recover_runtime(
                    max_events=args.max_events,
                    dry_run=bool(args.dry_run),
                    confirm=str(args.confirm or ""),
                )
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            return _emit_usage_error("ms8 shadow: choose status|health|seal|unseal|recover")
        if args.command == "skill":
            if args.skill_cmd == "list":
                out = list_skills_runtime()
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.skill_cmd == "install":
                out = install_skill_runtime(github_url=args.url, scope=args.scope)
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.skill_cmd == "uninstall":
                out = uninstall_skill_runtime(skill_name=args.name, scope=args.scope)
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.skill_cmd == "search":
                out = search_skills_runtime(query=args.query, category=args.category, limit=args.limit)
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.skill_cmd == "updates":
                out = skill_updates_runtime()
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.skill_cmd == "categories":
                out = skill_categories_runtime()
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.skill_cmd == "tags":
                out = skill_tags_runtime()
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.skill_cmd == "suggest":
                out = skill_suggest_runtime(prefix=args.prefix, limit=args.limit)
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.skill_cmd == "github-search":
                out = skill_github_search_runtime(
                    query=args.query,
                    category=args.category,
                    min_stars=args.min_stars,
                    sort_by=args.sort_by,
                    limit=args.limit,
                )
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.skill_cmd == "index-stats":
                out = skill_index_stats_runtime()
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            return _emit_usage_error(
                "ms8 skill: choose list|install|uninstall|search|updates|categories|tags|suggest|github-search|index-stats"
            )
        if args.command == "graph":
            if args.graph_cmd == "stats":
                out = graph_stats_runtime()
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.graph_cmd == "extract":
                out = graph_extract_runtime(limit=args.limit, force=bool(args.force))
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.graph_cmd == "maintain":
                out = graph_maint_runtime()
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.graph_cmd == "repair-access":
                out = graph_repair_access_runtime(min_access=args.min_access)
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.graph_cmd == "search":
                out = graph_search_entities_runtime(query=args.query, entity_type=args.entity_type, limit=args.limit)
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.graph_cmd == "relations":
                out = graph_list_relations_runtime(
                    entity_name=args.entity_name,
                    relation_type=args.relation_type,
                    direction=args.direction,
                    limit=args.limit,
                )
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.graph_cmd == "neighbors":
                out = graph_neighbors_runtime(
                    entity_name=args.entity_name,
                    depth=args.depth,
                    relation_type=args.relation_type,
                    limit=args.limit,
                )
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.graph_cmd == "path":
                out = graph_path_runtime(start_name=args.start, end_name=args.end, max_depth=args.max_depth)
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.graph_cmd == "timeline":
                out = graph_timeline_runtime(days=args.days, limit=args.limit)
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.graph_cmd == "health":
                out = graph_health_runtime()
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            return _emit_usage_error(
                "ms8 graph: choose stats|extract|maintain|repair-access|search|relations|neighbors|path|timeline|health"
            )
        if args.command == "review":
            if args.review_cmd in {"list", "status"}:
                out = review_list_runtime()
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.review_cmd == "batch":
                out = review_batch_runtime(
                    mode=args.mode,
                    limit=args.limit,
                    accept_conf_min=args.accept_conf_min,
                    reject_conf_max=args.reject_conf_max,
                    per_category_limit=args.per_category_limit,
                    drain_reject_conf_max=args.drain_reject_conf_max,
                )
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.review_cmd == "relabel":
                out = review_relabel_runtime(memory_id=args.memory_id, category=args.category, notes=args.notes)
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.review_cmd == "threshold-list":
                out = threshold_list_runtime(include_processed=bool(args.include_processed))
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.review_cmd == "threshold-approve":
                out = threshold_approve_runtime(approval_id=args.approval_id, approver=args.approver, confirm=True)
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.review_cmd == "threshold-reject":
                out = threshold_reject_runtime(approval_id=args.approval_id, approver=args.approver, reason=args.reason)
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            return _emit_usage_error(
                "ms8 review: choose list|batch|relabel|threshold-list|threshold-approve|threshold-reject"
            )
        if args.command == "ops":
            if args.ops_cmd in _LABS_EXPERIMENTAL_OPS_CMDS and not _read_labs_enabled():
                return _labs_gate_error(str(args.ops_cmd))
            if args.ops_cmd == "support-bundle":
                out = export_support_bundle_runtime(
                    output=str(getattr(args, "output", "") or ""),
                    redact=not bool(getattr(args, "no_redact", False)),
                    dry_run=bool(getattr(args, "dry_run", False)),
                )
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.ops_cmd == "self-check-report":
                out = self_check_report_runtime()
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.ops_cmd == "self-repair-run":
                out = self_repair_run_runtime(
                    mode=args.mode,
                    domain=args.domain,
                    check_id=args.check_id,
                    risk=args.risk,
                    approve_r3=bool(args.approve_r3),
                    auto=bool(args.auto),
                )
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.ops_cmd == "self-repair-report":
                out = self_repair_report_runtime()
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.ops_cmd == "self-repair-history":
                out = self_repair_history_runtime(limit=args.limit)
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.ops_cmd == "self-repair-rollback":
                out = self_repair_rollback_runtime(operation_id=args.operation_id)
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.ops_cmd == "dedupe-now":
                out = repair_duplicates_after_compression()
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.ops_cmd == "llm-status":
                out = get_engine_llm_status()
                print(json.dumps(out, ensure_ascii=False, indent=2))
                # Informational command: do not fail CLI when LLM is intentionally disabled/offline.
                return 0
            if args.ops_cmd == "weekly-compress":
                out = run_weekly_compression(confirm=bool(args.confirm))
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.ops_cmd == "archived-logs":
                out = list_archived_logs_runtime(limit=args.limit)
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.ops_cmd == "subagents":
                out = list_subagents_runtime()
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.ops_cmd == "subagent-tasks":
                out = list_subagent_tasks_runtime(limit=args.limit)
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.ops_cmd == "subagent-task":
                out = get_background_subagent_task_runtime(task_id=args.task_id)
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.ops_cmd == "validation-suite":
                out = run_validation_suite_runtime()
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.ops_cmd == "backfill-ids":
                out = backfill_auto_memory_ids_runtime()
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.ops_cmd == "cleanup-memory":
                out = cleanup_old_memory_runtime()
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.ops_cmd == "monitoring-status":
                out = monitoring_status_runtime()
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.ops_cmd == "governance":
                out = get_governance_report()
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0
            if args.ops_cmd == "compression-status":
                out = monitoring_status_runtime()
                result = out.get("result", {}) if isinstance(out, dict) else {}
                lifecycle = {
                    "compression_freshness": result.get("compression_freshness", {}),
                    "core_metrics": result.get("core_metrics", {}),
                    "maintenance_policy_stats": result.get("maintenance_policy_stats", {}),
                }
                print(json.dumps({"status": "success", "lifecycle": lifecycle}, ensure_ascii=False, indent=2))
                return 0
            if args.ops_cmd == "compression-run":
                if bool(args.dry_run):
                    out = preview_weekly_compression_runtime()
                    print(json.dumps(out, ensure_ascii=False, indent=2))
                    return 0 if bool(out.get("ok", False)) else 1
                out = run_weekly_compression(confirm=bool(args.confirm))
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.ops_cmd == "compression-repair":
                out = repair_duplicates_after_compression()
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.ops_cmd == "advanced-insight-status":
                out = advanced_insight_status_runtime()
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.ops_cmd == "meta-status":
                out = meta_cognition_status_runtime()
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.ops_cmd == "meta-run":
                out = run_meta_cognition_runtime(period=args.period)
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.ops_cmd == "context-blocks":
                out = get_context_with_blocks_runtime()
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.ops_cmd == "augmented-context":
                out = get_augmented_context_runtime(
                    message=args.message,
                    include_blocks=not bool(args.no_blocks),
                    graph_limit=args.graph_limit,
                )
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.ops_cmd == "synthetic-generate":
                out = generate_synthetic_candidates_runtime(limit=args.limit)
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.ops_cmd == "github-catalog":
                out = get_github_skill_catalog_runtime(org=args.org)
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.ops_cmd == "git-history":
                out = git_history_runtime(max_count=args.max_count)
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.ops_cmd == "git-commit":
                out = git_commit_runtime(message=args.message)
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.ops_cmd == "built-in-install":
                out = install_built_in_skill_runtime(skill_name=args.name)
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.ops_cmd == "built-in-install-all":
                out = install_all_built_in_skills_runtime()
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.ops_cmd == "skill-install-file":
                out = install_skill_from_file_runtime(file_path=args.path, scope=args.scope)
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.ops_cmd == "skill-install-search":
                out = install_skill_from_github_search_runtime(skill_name=args.name, repository=args.repository)
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.ops_cmd == "skill-install-registry":
                out = install_skill_from_registry_runtime(skill_id=args.skill_id, scope=args.scope)
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.ops_cmd == "git-available":
                out = is_git_available_runtime()
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.ops_cmd == "learning-enabled":
                out = is_learning_enabled_runtime()
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.ops_cmd == "learn-skill":
                raw = Path(args.trajectory_file).read_text(encoding="utf-8")
                data = json.loads(raw)
                if not isinstance(data, list):
                    return _emit_usage_error("ms8 ops learn-skill: --trajectory-file must contain a JSON list")
                out = learn_skill_runtime(trajectory=data, skill_name=args.skill_name, instructions=args.instructions)
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.ops_cmd == "skill-load-tool":
                out = load_skill_with_tool_runtime(skill_name=args.name)
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.ops_cmd == "weekly-compress-preview":
                out = preview_weekly_compression_runtime()
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.ops_cmd == "graph-offline-cleanup":
                out = prepare_graph_offline_cleanup_runtime(limit=args.limit)
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.ops_cmd == "purge-test-memory":
                out = purge_test_memory_data_runtime()
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.ops_cmd == "feedback-rebalance":
                out = rebalance_feedback_distribution_runtime(window=args.window)
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.ops_cmd == "skill-index-refresh":
                out = refresh_skill_index_runtime()
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.ops_cmd == "learning-run-pending":
                out = run_learning_tasks_runtime()
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.ops_cmd == "shadow-archive-spool":
                out = shadow_archive_spool_runtime()
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.ops_cmd == "short-term-restore":
                out = restore_short_term_by_topic_runtime(query=args.query, limit=args.limit)
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.ops_cmd == "subagent-retry":
                out = retry_background_subagent_task_runtime(task_id=args.task_id)
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            if args.ops_cmd == "subagent-spawn":
                out = spawn_subagent_runtime(
                    subagent_name=args.name,
                    task=args.task,
                    background=bool(args.background),
                )
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            return _emit_usage_error(
                "ms8 ops: choose support-bundle|self-check-report|self-repair-run|self-repair-report|self-repair-history|self-repair-rollback|dedupe-now|llm-status|weekly-compress|compression-status|compression-run|compression-repair|governance|archived-logs|subagents|subagent-tasks|subagent-task|validation-suite|backfill-ids|cleanup-memory|monitoring-status|advanced-insight-status|meta-status|meta-run|context-blocks|augmented-context|synthetic-generate|github-catalog|git-history|git-commit|built-in-install|built-in-install-all|skill-install-file|skill-install-search|skill-install-registry|git-available|learning-enabled|learn-skill|skill-load-tool|weekly-compress-preview|graph-offline-cleanup|purge-test-memory|feedback-rebalance|skill-index-refresh|learning-run-pending|shadow-archive-spool|short-term-restore|subagent-retry|subagent-spawn"
            )
        if args.command == "feedback":
            if args.feedback_cmd == "record":
                helpful = str(args.helpful).strip().lower() == "true"
                out = feedback_record_runtime(
                    memory_id=args.memory_id,
                    category=args.category,
                    signal=args.signal,
                    helpful=helpful,
                    note=args.note,
                    source=args.source,
                    confidence=args.confidence,
                )
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0 if bool(out.get("ok", False)) else 1
            return _emit_usage_error("ms8 feedback: choose record")
    except (OSError, ValueError) as exc:
        logger.error("ms8 runtime error: %s", exc)
        print(f"ms8 error: {exc}", file=sys.stderr)
        if isinstance(exc, PermissionError):
            hint = (
                "hint: current runtime path is not writable. "
                "Try: MS8_HOME=/path/to/writable/.ms8 ms8 <command>"
            )
            print(hint, file=sys.stderr)
        return 1

    parser.print_help()
    return 2
