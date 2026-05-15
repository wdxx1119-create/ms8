"""Minimal local runtime for MS8."""

from __future__ import annotations

import ast
import json
import logging
import os
import re
import shutil
import zipfile
from collections import Counter
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import requests
import yaml

from .compression_governance import cluster_duplicate_records
from .engine import get_engine, get_engine_status
from .paths import get_config_dir, get_data_dir, get_health_dir, get_log_dir, get_ms8_home
from .record_policy import (
    normalize_text,
    repair_scope_flags,
    validate_file_and_quarantine,
    validate_record,
)

logger = logging.getLogger(__name__)


def _runtime_error(reason: str, *, method: str = "", error_code: str = "E_RUNTIME_OPERATION_FAILED", ran: bool = False) -> dict[str, Any]:
    return {
        "ok": False,
        "ran": bool(ran),
        "reason": str(reason or ""),
        "method": str(method or ""),
        "error_code": str(error_code),
    }


def get_runtime_dir() -> Path:
    return get_ms8_home()


def _is_writable_dir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".ms8_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def ensure_runtime_dirs() -> dict[str, Path]:
    root = get_runtime_dir()
    data = get_data_dir()
    config = get_config_dir()
    logs = get_log_dir()
    health = get_health_dir()
    backups = root / "backups"
    for p in (root, data, config, backups, logs, health):
        p.mkdir(parents=True, exist_ok=True)
    engine = _engine()
    memories = engine.records_file() if hasattr(engine, "records_file") else data / "memories.jsonl"
    try:
        memories.parent.mkdir(parents=True, exist_ok=True)
        memories.touch(exist_ok=True)
    except OSError:
        memories = data / "memories.jsonl"
        memories.parent.mkdir(parents=True, exist_ok=True)
        memories.touch(exist_ok=True)
    activity = health / "activity.json"
    compression_state = root / "memory" / "compression_state.json"
    maintenance_window = health / "maintenance_window.json"
    quarantine_file = root / "memory" / "noncanonical_quarantine.jsonl"
    quarantine_file.parent.mkdir(parents=True, exist_ok=True)
    quarantine_file.touch(exist_ok=True)
    if not compression_state.exists():
        compression_state.write_text(
            json.dumps(
                {
                    "status": "initialized",
                    "last_run_at": "",
                    "last_result": {},
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    config_file = root / "config.json"
    if not config_file.exists():
        config_file.write_text(
            json.dumps(
                {
                    "governance_risk": {
                        "red": {
                            "schema_invalid_count_gt": 0,
                            "fallback_write_count_gt": 5,
                            "noncanonical_records_gt": 0,
                        },
                        "yellow": {
                            "fallback_write_count_gt": 0,
                            "pending_review_gt": 5,
                            "duplicate_groups_gt": 5,
                        },
                    },
                    "dedupe": {
                        "enabled": True,
                        "collapse_superseded": True,
                        "archive_file": "memory/archive/superseded_duplicates_auto.jsonl",
                    },
                    "governance_slo": {
                        "authority": "v2_preview",
                        "v2_min_eligible_events": 30,
                    },
                    "labs": {
                        "enabled": False,
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    try:
        repair_scope_flags(memories)
    except OSError as exc:
        logger.debug("Failed to repair scope flags during runtime dir init: %s", exc)
    try:
        validate_file_and_quarantine(memories, quarantine_file)
    except OSError as exc:
        logger.debug("Failed to validate memory file during runtime dir init: %s", exc)
    return {
        "root": root,
        "data": data,
        "backups": backups,
        "logs": logs,
        "config": config,
        "health": health,
        "memories": memories,
        "activity": activity,
        "compression_state": compression_state,
        "quarantine": quarantine_file,
        "maintenance_window": maintenance_window,
        "config_file": config_file,
    }


def maintenance_window_status() -> dict[str, Any]:
    paths = ensure_runtime_dirs()
    p = paths["maintenance_window"]
    if not p.exists():
        return {"enabled": False, "reason": ""}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"enabled": False, "reason": ""}
        return {
            "enabled": bool(data.get("enabled", False)),
            "reason": str(data.get("reason", "")),
            "started_at": str(data.get("started_at", "")),
            "pause_session_ingestion": bool(data.get("pause_session_ingestion", True)),
            "pause_maintenance_writes": bool(data.get("pause_maintenance_writes", True)),
            "pause_review_writes": bool(data.get("pause_review_writes", True)),
            "pause_compression_writes": bool(data.get("pause_compression_writes", True)),
        }
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        logger.debug("Failed to read maintenance window status: %s", exc)
        return {"enabled": False, "reason": ""}


def set_maintenance_window(
    enabled: bool,
    *,
    reason: str = "",
    pause_session_ingestion: bool = True,
    pause_maintenance_writes: bool = True,
    pause_review_writes: bool = True,
    pause_compression_writes: bool = True,
) -> dict[str, Any]:
    paths = ensure_runtime_dirs()
    p = paths["maintenance_window"]
    if not enabled:
        p.unlink(missing_ok=True)
        return {"enabled": False, "path": str(p)}
    payload = {
        "enabled": True,
        "reason": str(reason or "maintenance_window"),
        "started_at": _utc_now(),
        "pause_session_ingestion": bool(pause_session_ingestion),
        "pause_maintenance_writes": bool(pause_maintenance_writes),
        "pause_review_writes": bool(pause_review_writes),
        "pause_compression_writes": bool(pause_compression_writes),
    }
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"enabled": True, "path": str(p), "payload": payload}


def repair_memory_governance_flags() -> dict[str, int]:
    paths = ensure_runtime_dirs()
    return repair_scope_flags(paths["memories"])


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_aware(ts_text: str) -> datetime | None:
    raw = str(ts_text or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError as exc:
        logger.debug("Failed to parse timestamp '%s': %s", ts_text, exc)
        return None


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return raw
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        logger.debug("Failed to read JSON file %s: %s", path, exc)
    return {}


def _write_compression_state(
    *,
    status: str,
    ran: bool,
    reason: str,
    method: str = "",
    hours_since_last: Any = None,
    result: Any = None,
) -> None:
    paths = ensure_runtime_dirs()
    p = paths["compression_state"]
    prev = _read_json_file(p)
    now = _utc_now()
    failures = int(prev.get("consecutive_failures", 0) or 0)
    success = bool(ran and status == "success")
    if success:
        failures = 0
    elif ran:
        failures += 1
    payload = {
        "status": status,
        "last_run_at": now if ran else str(prev.get("last_run_at", "")),
        "last_attempt_at": now,
        "last_success_at": now if success else str(prev.get("last_success_at", "")),
        "consecutive_failures": failures,
        "last_reason": str(reason or ""),
        "last_method": str(method or ""),
        "hours_since_last_check": hours_since_last,
        "last_result": result if isinstance(result, dict) else {},
    }
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


@lru_cache(maxsize=1)
def _engine():
    root = get_runtime_dir()
    for p in (
        root,
        root / "memory",
        root / "data",
        root / "logs",
        root / "health",
        root / "backups",
    ):
        p.mkdir(parents=True, exist_ok=True)
    return get_engine(root)


def _touch_activity(event: str) -> None:
    paths = ensure_runtime_dirs()
    payload = {"event": event, "at": _utc_now()}
    paths["activity"].write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def has_recent_activity(window_seconds: int = 300) -> bool:
    paths = ensure_runtime_dirs()
    activity = paths["activity"]
    if not activity.exists():
        return False
    try:
        data = json.loads(activity.read_text(encoding="utf-8"))
        at = data.get("at")
        if not at:
            return False
        dt = datetime.fromisoformat(str(at))
        now = datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (now - dt).total_seconds() <= window_seconds
    except (json.JSONDecodeError, OSError, ValueError):
        return False


def write_memory(text: str, source: str = "demo") -> dict:
    mw = maintenance_window_status()
    if bool(mw.get("enabled", False)):
        src = str(source or "").strip().lower()
        auto_sources = {
            "maintenance",
            "review",
            "compression",
            "session_ingestion",
            "system",
            "watch",
            "doctor",
        }
        if src in auto_sources:
            return {
                "status": "skipped",
                "reason": "maintenance_window",
                "source": source,
                "text": str(text or ""),
            }
    rec = _engine().write_memory(text=text, source=source)
    try:
        paths = ensure_runtime_dirs()
        validate_file_and_quarantine(paths["memories"], paths["quarantine"])
    except OSError as exc:
        logger.debug("Failed post-write quarantine validation: %s", exc)
    _touch_activity("write")
    return rec


def read_memories() -> list[dict]:
    rows = _engine().read_memories()
    _touch_activity("read")
    return rows


def search_memories(query: str, limit: int = 50) -> list[dict]:
    rows = _engine().search_memories(query=query, limit=limit)
    _touch_activity("search")
    return rows


def count_memories() -> int:
    return _engine().count_memories()


def last_write_time() -> str | None:
    return _engine().last_write_time()


def backup_memories(tag: str = "manual") -> dict:
    paths = ensure_runtime_dirs()
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_file = paths["backups"] / f"memories-{tag}-{ts}.jsonl"
    shutil.copy2(paths["memories"], backup_file)
    return {"ok": True, "path": str(backup_file)}


def cleanup_old_backups(max_keep: int = 20) -> dict:
    paths = ensure_runtime_dirs()
    backups = sorted([p for p in paths["backups"].glob("memories-*.jsonl") if p.is_file()])
    removed: list[str] = []
    if len(backups) > max_keep:
        for p in backups[: len(backups) - max_keep]:
            p.unlink(missing_ok=True)
            removed.append(str(p))
    return {"ok": True, "removed_count": len(removed), "removed": removed}


def engine_status() -> dict:
    return get_engine_status(get_runtime_dir())


def run_maintenance_policy() -> dict[str, object]:
    mw = maintenance_window_status()
    if bool(mw.get("enabled", False)) and bool(mw.get("pause_maintenance_writes", True)):
        return {"ok": True, "ran": False, "reason": "maintenance_window"}
    engine = _engine()
    core = getattr(engine, "_core", None)
    if core is None:
        return {"ok": False, "ran": False, "reason": "core_unavailable"}
    for name in ("maintenance_policy", "run_maintenance_policy", "auto_maintenance"):
        fn = getattr(core, name, None)
        if fn is None:
            continue
        try:
            result = fn()
            return {"ok": True, "ran": True, "method": name, "result": result}
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning("runtime_maintenance_policy_failed method=%s err=%s", name, exc)
            return _runtime_error(str(exc), method=name, error_code="E_RUNTIME_MAINTENANCE_POLICY_FAILED")
    return {"ok": False, "ran": False, "reason": "method_missing"}


def run_maintenance_now(force: bool = True) -> dict[str, object]:
    mw = maintenance_window_status()
    if bool(mw.get("enabled", False)) and bool(mw.get("pause_maintenance_writes", True)):
        return {"ok": True, "ran": False, "reason": "maintenance_window"}
    engine = _engine()
    if hasattr(engine, "run_maintenance_now"):
        try:
            result = engine.run_maintenance_now(force=force)
            return {
                "ok": True,
                "ran": True,
                "method": "engine.run_maintenance_now",
                "result": result,
            }
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning("runtime_maintenance_now_failed err=%s", exc)
            return _runtime_error(
                str(exc),
                method="engine.run_maintenance_now",
                error_code="E_RUNTIME_MAINTENANCE_NOW_FAILED",
            )
    return run_maintenance_policy()


def run_daily_learning(date_str: str | None = None) -> dict[str, object]:
    mw = maintenance_window_status()
    if bool(mw.get("enabled", False)) and bool(mw.get("pause_maintenance_writes", True)):
        return {"ok": True, "ran": False, "reason": "maintenance_window"}
    engine = _engine()
    core = getattr(engine, "_core", None)
    if core is None or not hasattr(core, "trigger_daily_learning"):
        return {"ok": False, "ran": False, "reason": "trigger_daily_learning_unavailable"}
    try:
        core.trigger_daily_learning(date_str=date_str)
        return {"ok": True, "ran": True, "method": "core.trigger_daily_learning"}
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.warning("runtime_daily_learning_failed err=%s", exc)
        return _runtime_error(
            str(exc),
            method="core.trigger_daily_learning",
            error_code="E_RUNTIME_DAILY_LEARNING_FAILED",
        )


def _run_core_method(name: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
    engine = _engine()
    core = getattr(engine, "_core", None)
    if core is None:
        return {"ok": False, "ran": False, "reason": "core_unavailable", "method": name}
    fn = getattr(core, name, None)
    if fn is None:
        return {"ok": False, "ran": False, "reason": "method_missing", "method": name}
    try:
        result = fn(*args, **kwargs)
        return {"ok": True, "ran": True, "method": name, "result": result}
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.warning("runtime_core_method_failed method=%s err=%s", name, exc)
        return _runtime_error(str(exc), method=name, error_code="E_RUNTIME_CORE_METHOD_FAILED")


def run_kg_batch_extract(limit: int = 20, force: bool = False) -> dict[str, Any]:
    return _run_core_method("batch_extract_knowledge_graph", limit=max(1, int(limit)), force=bool(force))


def run_memory_tiering() -> dict[str, Any]:
    return _run_core_method("trigger_memory_tiering")


def run_graph_maintenance() -> dict[str, Any]:
    return _run_core_method("run_knowledge_graph_maintenance")


def run_reflection() -> dict[str, Any]:
    return _run_core_method("trigger_reflection")


def run_synthetic_auto_confirm() -> dict[str, Any]:
    return _run_core_method("auto_confirm_synthetic_candidates")


def run_weekly_compression(confirm: bool = False) -> dict[str, Any]:
    out = _run_core_method("trigger_weekly_compression", confirm=bool(confirm))
    ok = bool(out.get("ok", False))
    result = out.get("result", {}) if isinstance(out, dict) else {}
    status = str(result.get("status", "success" if ok else "error")) if isinstance(result, dict) else ("success" if ok else "error")
    reason = str(result.get("status", "")) if isinstance(result, dict) else ""
    _write_compression_state(
        status=status if ok else "error",
        ran=ok,
        reason=reason or ("triggered" if ok else "failed"),
        method="trigger_weekly_compression",
        result=out if isinstance(out, dict) else {},
    )
    if ok:
        try:
            paths = ensure_runtime_dirs()
            report_dir = paths["memory"] / "compression_reports"
            report_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            payload = {
                "timestamp": _utc_now(),
                "method": "trigger_weekly_compression",
                "confirm": bool(confirm),
                "result": result if isinstance(result, dict) else {},
            }
            (report_dir / f"compression_{stamp}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.debug("Failed to persist compression report artifact: %s", exc)
    return out


def list_archived_logs_runtime(limit: int = 50) -> dict[str, Any]:
    return _run_core_method("list_archived_logs", limit=max(1, int(limit)))


def list_subagents_runtime() -> dict[str, Any]:
    return _run_core_method("list_subagents")


def list_subagent_tasks_runtime(limit: int = 20) -> dict[str, Any]:
    return _run_core_method("list_background_subagent_tasks", limit=max(1, int(limit)))


def run_validation_suite_runtime() -> dict[str, Any]:
    return _run_core_method("run_validation_suite")


def backfill_auto_memory_ids_runtime() -> dict[str, Any]:
    return _run_core_method("backfill_auto_memory_record_ids")


def cleanup_old_memory_runtime() -> dict[str, Any]:
    return _run_core_method("cleanup_old_memory")


def monitoring_status_runtime() -> dict[str, Any]:
    return _run_core_method("get_monitoring_status")


def advanced_insight_status_runtime() -> dict[str, Any]:
    return _run_core_method("get_advanced_insight_status")


def meta_cognition_status_runtime() -> dict[str, Any]:
    return _run_core_method("get_meta_cognition_status")


def run_meta_cognition_runtime(period: str | None = None) -> dict[str, Any]:
    if period:
        return _run_core_method("run_meta_cognition", period=period)
    return _run_core_method("run_meta_cognition")


def add_to_short_term_runtime(item: str, source: str = "cli") -> dict[str, Any]:
    return _run_core_method("add_to_short_term", item=item, source=source)


def create_subagent_runtime(
    name: str,
    description: str,
    instructions: str,
    tools: list[str] | None = None,
) -> dict[str, Any]:
    return _run_core_method("create_subagent", name, description, instructions, list(tools or []))


def add_skill_registry_runtime(name: str, url: str, reg_type: str = "github") -> dict[str, Any]:
    return _run_core_method("add_skill_registry", name, url, reg_type)


def add_validation_test_runtime(
    test_type: str,
    name: str,
    input_data: dict[str, Any],
    expected_output: Any,
) -> dict[str, Any]:
    return _run_core_method("add_validation_test", test_type, name, dict(input_data or {}), expected_output)


def discover_synthetic_gaps_runtime(limit: int = 10) -> dict[str, Any]:
    return _run_core_method("discover_synthetic_gaps", limit=max(1, int(limit)))


def generate_synthetic_candidates_runtime(limit: int = 20) -> dict[str, Any]:
    return _run_core_method("generate_synthetic_candidates", limit=max(1, int(limit)))


def get_context_with_blocks_runtime() -> dict[str, Any]:
    return _run_core_method("get_context_with_blocks")


def get_augmented_context_runtime(message: str, include_blocks: bool = True, graph_limit: int = 5) -> dict[str, Any]:
    return _run_core_method(
        "get_augmented_context",
        message=str(message or ""),
        include_blocks=bool(include_blocks),
        graph_limit=max(1, int(graph_limit)),
    )


def get_background_subagent_task_runtime(task_id: str) -> dict[str, Any]:
    return _run_core_method("get_background_subagent_task", task_id=str(task_id or ""))


def get_github_skill_catalog_runtime(org: str = "openclaw") -> dict[str, Any]:
    return _run_core_method("get_github_skill_catalog", org=str(org or "openclaw"))


def get_graph_context_runtime(message: str, limit: int = 5) -> dict[str, Any]:
    return _run_core_method("get_graph_context", message=str(message or ""), limit=max(1, int(limit)))


def get_graph_related_entities_runtime(entity_name: str, limit: int = 10) -> dict[str, Any]:
    return _run_core_method("get_graph_related_entities", entity_name=str(entity_name or ""), limit=max(1, int(limit)))


def get_self_improvement_metrics_runtime() -> dict[str, Any]:
    return _run_core_method("get_self_improvement_metrics")


def get_system_prompt_with_skills_runtime() -> dict[str, Any]:
    return _run_core_method("get_system_prompt_with_skills")


def git_commit_runtime(message: str | None = None) -> dict[str, Any]:
    if message is None:
        return _run_core_method("git_commit")
    return _run_core_method("git_commit", message=str(message))


def git_history_runtime(max_count: int = 10) -> dict[str, Any]:
    return _run_core_method("git_history", max_count=max(1, int(max_count)))


def install_built_in_skill_runtime(skill_name: str) -> dict[str, Any]:
    return _run_core_method("install_built_in_skill", skill_name=str(skill_name or ""))


def install_all_built_in_skills_runtime() -> dict[str, Any]:
    return _run_core_method("install_all_built_in_skills")


def install_skill_from_file_runtime(file_path: str, scope: str = "project") -> dict[str, Any]:
    return _run_core_method("install_skill_from_file", file_path=str(file_path or ""), scope=str(scope or "project"))


def install_skill_from_github_search_runtime(skill_name: str, repository: str | None = None) -> dict[str, Any]:
    if repository:
        return _run_core_method("install_skill_from_github_search", skill_name=str(skill_name), repository=str(repository))
    return _run_core_method("install_skill_from_github_search", skill_name=str(skill_name))


def install_skill_from_registry_runtime(skill_id: str, scope: str = "project") -> dict[str, Any]:
    return _run_core_method("install_skill_from_registry", skill_id=str(skill_id or ""), scope=str(scope or "project"))


def is_git_available_runtime() -> dict[str, Any]:
    return _run_core_method("is_git_available")


def is_learning_enabled_runtime() -> dict[str, Any]:
    return _run_core_method("is_learning_enabled")


def learn_skill_runtime(trajectory: list[dict[str, Any]], skill_name: str, instructions: str | None = None) -> dict[str, Any]:
    if instructions:
        return _run_core_method(
            "learn_skill",
            trajectory=list(trajectory or []),
            skill_name=str(skill_name or ""),
            instructions=str(instructions),
        )
    return _run_core_method("learn_skill", trajectory=list(trajectory or []), skill_name=str(skill_name or ""))


def load_skill_with_tool_runtime(skill_name: str) -> dict[str, Any]:
    return _run_core_method("load_skill_with_tool", skill_name=str(skill_name or ""))


def prepare_graph_offline_cleanup_runtime(limit: int = 500) -> dict[str, Any]:
    return _run_core_method("prepare_graph_offline_cleanup", limit=max(1, int(limit)))


def preview_weekly_compression_runtime() -> dict[str, Any]:
    return _run_core_method("preview_weekly_compression")


def purge_test_memory_data_runtime() -> dict[str, Any]:
    return _run_core_method("purge_test_memory_data")


def rebalance_feedback_distribution_runtime(window: int | None = None) -> dict[str, Any]:
    if window is None:
        return _run_core_method("rebalance_feedback_distribution")
    return _run_core_method("rebalance_feedback_distribution", window=max(1, int(window)))


def refresh_skill_index_runtime() -> dict[str, Any]:
    return _run_core_method("refresh_skill_index")


def restore_short_term_by_topic_runtime(query: str, limit: int = 20) -> dict[str, Any]:
    return _run_core_method("restore_short_term_by_topic", query=str(query or ""), limit=max(1, int(limit)))


def retry_background_subagent_task_runtime(task_id: str) -> dict[str, Any]:
    return _run_core_method("retry_background_subagent_task", task_id=str(task_id or ""))


def run_learning_tasks_runtime() -> dict[str, Any]:
    return _run_core_method("run_learning_tasks")


def shadow_archive_spool_runtime() -> dict[str, Any]:
    return _run_core_method("shadow_archive_spool")


def spawn_subagent_runtime(subagent_name: str, task: str, background: bool = False) -> dict[str, Any]:
    return _run_core_method(
        "spawn_subagent",
        subagent_name=str(subagent_name or ""),
        task=str(task or ""),
        background=bool(background),
    )


def security_status_runtime() -> dict[str, Any]:
    out = _run_core_method("security_status")
    return out.get("result", out) if out.get("ok", False) else out


def security_enable_runtime(master_password: str) -> dict[str, Any]:
    return _run_core_method("security_enable", master_password)


def security_disable_runtime(master_password: str) -> dict[str, Any]:
    return _run_core_method("security_disable", master_password)


def security_unlock_runtime(master_password: str) -> dict[str, Any]:
    return _run_core_method("security_unlock", master_password)


def security_lock_runtime() -> dict[str, Any]:
    return _run_core_method("security_lock")


def security_recover_runtime(recovery_key: str, new_master_password: str) -> dict[str, Any]:
    return _run_core_method("security_recover", recovery_key, new_master_password)


def shadow_status_runtime() -> dict[str, Any]:
    out = _run_core_method("shadow_status")
    return out.get("result", out) if out.get("ok", False) else out


def shadow_health_runtime() -> dict[str, Any]:
    return _run_core_method("shadow_health")


def shadow_seal_runtime(reason: str = "manual", level: str = "hard") -> dict[str, Any]:
    return _run_core_method("shadow_seal", reason=reason, level=level)


def shadow_unseal_runtime(reason: str = "manual") -> dict[str, Any]:
    return _run_core_method("shadow_unseal", reason=reason)


def shadow_recover_runtime(
    max_events: int = 200,
    *,
    dry_run: bool = False,
    confirm: str = "",
) -> dict[str, Any]:
    if bool(dry_run):
        return {
            "ok": True,
            "dry_run": True,
            "status": "preview",
            "action": "shadow_recover_from_events",
            "max_events": max(1, int(max_events)),
            "confirm_required": "SHADOW_RECOVERY",
        }
    if str(confirm or "").strip() != "SHADOW_RECOVERY":
        return {
            "ok": False,
            "status": "error",
            "error": "confirm_required",
            "required_confirm": "SHADOW_RECOVERY",
        }
    return _run_core_method("shadow_recover_from_events", max_events=max(1, int(max_events)))


def list_skills_runtime() -> dict[str, Any]:
    return _run_core_method("list_installed_skills")


def install_skill_runtime(github_url: str, scope: str = "project") -> dict[str, Any]:
    return _run_core_method("install_skill_from_github", github_url=github_url, scope=scope)


def uninstall_skill_runtime(skill_name: str, scope: str = "project") -> dict[str, Any]:
    return _run_core_method("uninstall_skill", skill_name=skill_name, scope=scope)


def search_skills_runtime(query: str, category: str | None = None, limit: int = 20) -> dict[str, Any]:
    return _run_core_method("search_skills_local", query=query, category=category, limit=max(1, int(limit)))


def skill_updates_runtime() -> dict[str, Any]:
    return _run_core_method("check_skill_updates")


def skill_categories_runtime() -> dict[str, Any]:
    return _run_core_method("get_skill_categories")


def skill_tags_runtime() -> dict[str, Any]:
    return _run_core_method("get_skill_tags")


def skill_suggest_runtime(prefix: str, limit: int = 5) -> dict[str, Any]:
    return _run_core_method("suggest_skills", prefix=prefix, limit=max(1, int(limit)))


def skill_github_search_runtime(
    query: str | None = None,
    category: str | None = None,
    min_stars: int = 0,
    sort_by: str = "stars",
    limit: int = 20,
) -> dict[str, Any]:
    return _run_core_method(
        "search_github_skills",
        query=query,
        category=category,
        min_stars=max(0, int(min_stars)),
        sort_by=sort_by,
        limit=max(1, int(limit)),
    )


def skill_index_stats_runtime() -> dict[str, Any]:
    return _run_core_method("get_index_stats")


def graph_stats_runtime() -> dict[str, Any]:
    return _run_core_method("get_knowledge_graph_stats")


def graph_extract_runtime(limit: int = 20, force: bool = False) -> dict[str, Any]:
    return _run_core_method("batch_extract_knowledge_graph", limit=max(1, int(limit)), force=bool(force))


def graph_maint_runtime() -> dict[str, Any]:
    return _run_core_method("run_knowledge_graph_maintenance")


def graph_repair_access_runtime(min_access: int = 1) -> dict[str, Any]:
    return _run_core_method("repair_graph_access_counts", min_access=max(1, int(min_access)))


def graph_search_entities_runtime(query: str, entity_type: str | None = None, limit: int = 10) -> dict[str, Any]:
    return _run_core_method(
        "search_graph_entities",
        query=query,
        entity_type=entity_type,
        limit=max(1, int(limit)),
    )


def graph_list_relations_runtime(
    entity_name: str = "",
    relation_type: str | None = None,
    direction: str = "both",
    limit: int = 10,
) -> dict[str, Any]:
    return _run_core_method(
        "list_graph_relations",
        entity_name=entity_name or None,
        relation_type=relation_type,
        direction=direction,
        limit=max(1, int(limit)),
    )


def graph_neighbors_runtime(entity_name: str, depth: int = 2, relation_type: str | None = None, limit: int = 10) -> dict[str, Any]:
    return _run_core_method(
        "get_graph_neighbors",
        entity_name=entity_name,
        depth=max(1, int(depth)),
        relation_type=relation_type,
        limit=max(1, int(limit)),
    )


def graph_path_runtime(start_name: str, end_name: str, max_depth: int = 3) -> dict[str, Any]:
    return _run_core_method(
        "find_graph_path",
        start_name=start_name,
        end_name=end_name,
        max_depth=max(1, int(max_depth)),
    )


def graph_timeline_runtime(days: int = 7, limit: int = 10) -> dict[str, Any]:
    return _run_core_method("get_knowledge_graph_timeline", days=max(1, int(days)), limit=max(1, int(limit)))


def graph_health_runtime() -> dict[str, Any]:
    return _run_core_method("get_knowledge_graph_health")


def review_list_runtime() -> dict[str, Any]:
    return _run_core_method("list_pending_reviews")


def review_batch_runtime(
    mode: str = "triage_default",
    limit: int = 30,
    accept_conf_min: float = 0.62,
    reject_conf_max: float = 0.20,
    per_category_limit: int = 6,
    drain_reject_conf_max: float = 0.50,
) -> dict[str, Any]:
    return _run_core_method(
        "batch_review",
        mode=mode,
        limit=max(1, int(limit)),
        accept_conf_min=float(accept_conf_min),
        reject_conf_max=float(reject_conf_max),
        per_category_limit=max(1, int(per_category_limit)),
        drain_reject_conf_max=float(drain_reject_conf_max),
    )


def review_relabel_runtime(memory_id: str, category: str, notes: str = "") -> dict[str, Any]:
    return _run_core_method("relabel_review_item", memory_id=memory_id, category=category, notes=notes)


def threshold_list_runtime(include_processed: bool = False) -> dict[str, Any]:
    return _run_core_method("list_pending_threshold_suggestions", include_processed=bool(include_processed))


def threshold_approve_runtime(approval_id: str, approver: str = "cli", confirm: bool = True) -> dict[str, Any]:
    return _run_core_method(
        "approve_threshold_suggestion",
        approval_id=approval_id,
        approver=approver,
        confirm=bool(confirm),
    )


def threshold_reject_runtime(approval_id: str, approver: str = "cli", reason: str = "manual_reject") -> dict[str, Any]:
    return _run_core_method(
        "reject_threshold_suggestion",
        approval_id=approval_id,
        approver=approver,
        reason=reason,
    )


def self_repair_run_runtime(
    mode: str = "dry-run",
    *,
    domain: str = "",
    check_id: str = "",
    risk: str = "",
    approve_r3: bool = False,
    auto: bool = False,
) -> dict[str, Any]:
    return _run_core_method(
        "run_self_repair",
        mode=mode,
        domain=domain,
        check_id=check_id,
        risk=risk,
        approve_r3=bool(approve_r3),
        auto=bool(auto),
    )


def self_repair_report_runtime() -> dict[str, Any]:
    return _run_core_method("get_self_repair_report")


def self_repair_history_runtime(limit: int = 10) -> dict[str, Any]:
    return _run_core_method("get_self_repair_history", limit=max(1, int(limit)))


def self_repair_rollback_runtime(operation_id: str) -> dict[str, Any]:
    return _run_core_method("rollback_self_repair_operation", operation_id=operation_id)


def self_check_report_runtime() -> dict[str, Any]:
    return _run_core_method("get_self_check_report")


def synthetic_list_runtime(status: str = "review", limit: int = 20) -> dict[str, Any]:
    return _run_core_method("list_synthetic_candidates", status=status, limit=max(1, int(limit)))


def synthetic_confirm_runtime(candidate_ids: list[str] | None = None, min_score: float | None = None) -> dict[str, Any]:
    return _run_core_method("confirm_synthetic_candidates", candidate_ids=candidate_ids, min_score=min_score)


def synthetic_reject_runtime(candidate_ids: list[str]) -> dict[str, Any]:
    return _run_core_method("reject_synthetic_candidates", candidate_ids=candidate_ids)


def synthetic_review_runtime(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    return _run_core_method("review_synthetic_candidates", decisions=decisions)


def synthetic_health_runtime() -> dict[str, Any]:
    return _run_core_method("get_synthetic_health")


def synthetic_rebalance_runtime(max_auto_accept: int = 40, apply_writeback: bool = False) -> dict[str, Any]:
    return _run_core_method(
        "rebalance_synthetic_candidates",
        max_auto_accept=max(1, int(max_auto_accept)),
        apply_writeback=bool(apply_writeback),
    )


def feedback_record_runtime(
    memory_id: str,
    category: str,
    signal: str,
    helpful: bool,
    note: str = "",
    source: str = "user",
    confidence: float = 0.0,
) -> dict[str, Any]:
    return _run_core_method(
        "record_memory_feedback",
        memory_id=memory_id,
        category=category,
        signal=signal,
        helpful=bool(helpful),
        note=note,
        source=source,
        confidence=float(confidence),
    )


def repair_compression_if_stale(stale_hours_threshold: int = 48) -> dict[str, object]:
    engine = _engine()
    core = getattr(engine, "_core", None)
    if core is None:
        out = {"ok": False, "ran": False, "reason": "core_unavailable"}
        _write_compression_state(status="error", ran=False, reason="core_unavailable", result=out)
        return out
    try:
        mon = core.get_monitoring_status() if hasattr(core, "get_monitoring_status") else {}
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.warning("runtime_monitoring_status_failed err=%s", exc)
        out = _runtime_error(
            f"monitoring_error:{exc}",
            method="core.get_monitoring_status",
            error_code="E_RUNTIME_MONITORING_STATUS_FAILED",
        )
        _write_compression_state(status="error", ran=False, reason=str(out["reason"]), result=out)
        return out

    stale = False
    hours_since_last = None
    if isinstance(mon, dict):
        freshness = mon.get("compression_freshness", {})
        if isinstance(freshness, dict):
            hours_since_last = freshness.get("hours_since_last")
            if isinstance(hours_since_last, (int, float)) and hours_since_last >= stale_hours_threshold:
                stale = True
        alerts = mon.get("alerts", [])
        if isinstance(alerts, list):
            if any(isinstance(a, dict) and str(a.get("kind", "")) == "compression_stale" for a in alerts):
                stale = True

    if not stale:
        out = {
            "ok": True,
            "ran": False,
            "reason": "not_stale",
            "hours_since_last": hours_since_last,
        }
        _write_compression_state(
            status="skipped",
            ran=False,
            reason="not_stale",
            hours_since_last=hours_since_last,
            result=out,
        )
        return out

    if hasattr(core, "trigger_weekly_compression"):
        try:
            result = core.trigger_weekly_compression(confirm=True)
            out = {
                "ok": True,
                "ran": True,
                "method": "trigger_weekly_compression",
                "result": result,
            }
            _write_compression_state(
                status="success",
                ran=True,
                reason="triggered",
                method="trigger_weekly_compression",
                hours_since_last=hours_since_last,
                result=out,
            )
            return out
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning("runtime_trigger_weekly_compression_failed err=%s", exc)
            out = {
                "ok": False,
                "ran": False,
                "reason": str(exc),
                "method": "trigger_weekly_compression",
                "error_code": "E_RUNTIME_TRIGGER_WEEKLY_COMPRESSION_FAILED",
            }
            _write_compression_state(
                status="error",
                ran=False,
                reason=str(exc),
                method="trigger_weekly_compression",
                hours_since_last=hours_since_last,
                result=out,
            )
            return out

    if hasattr(core, "run_maintenance_now"):
        try:
            result = core.run_maintenance_now(force=True)
            out = {"ok": True, "ran": True, "method": "run_maintenance_now", "result": result}
            _write_compression_state(
                status="success",
                ran=True,
                reason="triggered",
                method="run_maintenance_now",
                hours_since_last=hours_since_last,
                result=out,
            )
            return out
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning("runtime_run_maintenance_now_for_compression_failed err=%s", exc)
            out = {"ok": False, "ran": False, "reason": str(exc), "method": "run_maintenance_now"}
            out["error_code"] = "E_RUNTIME_COMPRESSION_MAINTENANCE_NOW_FAILED"
            _write_compression_state(
                status="error",
                ran=False,
                reason=str(exc),
                method="run_maintenance_now",
                hours_since_last=hours_since_last,
                result=out,
            )
            return out

    out = {"ok": False, "ran": False, "reason": "no_repair_method"}
    _write_compression_state(
        status="error",
        ran=False,
        reason="no_repair_method",
        hours_since_last=hours_since_last,
        result=out,
    )
    return out


def repair_duplicates_after_compression() -> dict[str, object]:
    paths = ensure_runtime_dirs()
    report_path = paths["health"] / "compression_duplicate_cluster_latest.json"
    cfg: dict[str, Any] = {}
    try:
        cfg_raw = json.loads(paths["config_file"].read_text(encoding="utf-8"))
        if isinstance(cfg_raw, dict):
            cfg = cfg_raw.get("dedupe", {}) if isinstance(cfg_raw.get("dedupe", {}), dict) else {}
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        logger.debug("Failed to load dedupe config for duplicate repair: %s", exc)
        cfg = {}
    enabled = bool(cfg.get("enabled", True))
    collapse = bool(cfg.get("collapse_superseded", True))
    archive_rel = str(cfg.get("archive_file", "memory/archive/superseded_duplicates_auto.jsonl"))
    archive_file = paths["root"] / archive_rel
    if not enabled:
        return {
            "ok": True,
            "result": {"status": "skipped", "reason": "dedupe_disabled"},
            "report_file": str(report_path),
        }
    out = cluster_duplicate_records(
        records_file=paths["memories"],
        report_file=report_path,
        collapse_superseded=collapse,
        superseded_archive_file=archive_file,
    )
    return {"ok": out.get("status") == "success", "result": out, "report_file": str(report_path)}


def run_engine_self_check(level: str = "L4") -> dict:
    engine = _engine()
    if hasattr(engine, "run_self_check"):
        return engine.run_self_check(level=level)
    return {"status": "unsupported", "level": level, "results": []}


_SUPPORT_BUNDLE_TEXT_MAX_BYTES = 2_000_000


def _support_bundle_candidates(root: Path) -> list[Path]:
    candidates = [
        root / "health" / "governance_report_latest.json",
        root / "health" / "governance_report_history.jsonl",
        root / "health" / "review_governance_latest.json",
        root / "health" / "activity.json",
        root / "memory" / "reports" / "self_check_latest.json",
        root / "memory" / "reports" / "self_check_latest.md",
        root / "memory" / "reports" / "self_repair_latest.json",
        root / "memory" / "reports" / "self_repair_latest.md",
        root / "memory" / "reports" / "self_repair_history.jsonl",
        root / "connect" / "runtime" / "connect_report.json",
        root / "connect" / "runtime" / "health.json",
        root / "config.json",
        root / "config.yaml",
        root / "config.project.yaml",
        root / "config.local.yaml",
    ]
    return [p for p in candidates if p.exists() and p.is_file()]


def _redact_support_text(text: str) -> str:
    out = str(text or "")
    rules = [
        (r"(sk-[A-Za-z0-9_\-]{12,})", "[REDACTED_TOKEN]"),
        (r"(ghp_[A-Za-z0-9]{20,})", "[REDACTED_TOKEN]"),
        (r"(github_pat_[A-Za-z0-9_]{20,})", "[REDACTED_TOKEN]"),
        (r"(AKIA[0-9A-Z]{16})", "[REDACTED_TOKEN]"),
        (r"(?i)(bearer\s+)[A-Za-z0-9\-\._~\+\/]+=*", r"\1[REDACTED_TOKEN]"),
        (r"(?i)(api[_-]?key\"?\s*[:=]\s*\")([^\"]+)(\")", r"\1[REDACTED_TOKEN]\3"),
        (r"(?i)(password\"?\s*[:=]\s*\")([^\"]+)(\")", r"\1[REDACTED_PASSWORD]\3"),
        (r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[REDACTED_EMAIL]"),
        (r"(?<!\d)(1[3-9]\d{9})(?!\d)", "[REDACTED_PHONE]"),
        (r"/Users/[^/\\\"\\s]+", "/Users/<redacted>"),
    ]
    for pattern, repl in rules:
        out = re.sub(pattern, repl, out)
    return out


def export_support_bundle_runtime(
    *,
    output: str | None = None,
    redact: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    paths = ensure_runtime_dirs()
    root = paths["root"]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = (
        Path(output).expanduser()
        if output is not None and str(output).strip()
        else (root / "health" / f"support_bundle_{stamp}.zip")
    )
    files = _support_bundle_candidates(root)
    rel_files = [str(p.relative_to(root)) for p in files]
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "output": str(out_path),
            "root": str(root),
            "redact": bool(redact),
            "files": rel_files,
            "count": len(rel_files),
        }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    added = []
    skipped = []
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for fp in files:
            rel = str(fp.relative_to(root))
            try:
                raw = fp.read_text(encoding="utf-8")
                if len(raw.encode("utf-8", errors="ignore")) > _SUPPORT_BUNDLE_TEXT_MAX_BYTES:
                    skipped.append({"file": rel, "reason": "too_large"})
                    continue
                content = _redact_support_text(raw) if bool(redact) else raw
                zf.writestr(rel, content)
                added.append(rel)
            except (OSError, UnicodeError, ValueError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
                skipped.append({"file": rel, "reason": str(exc)})
        manifest = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "root": str(root),
            "redact": bool(redact),
            "files_added": added,
            "files_skipped": skipped,
        }
        zf.writestr("bundle_manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return {
        "ok": True,
        "dry_run": False,
        "output": str(out_path),
        "root": str(root),
        "redact": bool(redact),
        "files_added": added,
        "files_skipped": skipped,
        "count": len(added),
    }


def get_engine_monitoring_status() -> dict:
    engine = _engine()
    if hasattr(engine, "get_monitoring_status"):
        return engine.get_monitoring_status()
    return {"enabled": False}


def get_engine_shadow_status() -> dict:
    engine = _engine()
    if hasattr(engine, "shadow_status"):
        return engine.shadow_status()
    return {"status": "unsupported"}


def get_engine_knowledge_graph_stats() -> dict:
    engine = _engine()
    if hasattr(engine, "get_knowledge_graph_stats"):
        return engine.get_knowledge_graph_stats()
    return {"entity_total": 0, "relation_total": 0}


def get_engine_llm_status() -> dict:
    engine = _engine()
    # Preferred adapter method on Engine facade.
    if hasattr(engine, "get_model_info"):
        try:
            info = engine.get_model_info()
            if isinstance(info, dict):
                return info
        except (AttributeError, TypeError) as exc:
            logger.debug("Engine facade get_model_info failed: %s", exc)
    # Fallback to core.
    core = getattr(engine, "_core", None)
    if core is not None and hasattr(core, "get_model_info"):
        try:
            info = core.get_model_info()
            if isinstance(info, dict):
                return info
        except (AttributeError, TypeError) as exc:
            logger.debug("Core get_model_info fallback failed: %s", exc)
    return {"available": False, "providers": {}, "message": "llm status unavailable"}


def _workspace_config_path() -> Path:
    paths = ensure_runtime_dirs()
    return paths["root"] / "config.yaml"


def _read_workspace_config_yaml() -> dict[str, Any]:
    cfg_file = _workspace_config_path()
    if not cfg_file.exists():
        return {}
    try:
        payload = yaml.safe_load(cfg_file.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return payload
    except (OSError, yaml.YAMLError, TypeError) as exc:
        logger.debug("Failed to parse workspace YAML config: %s", exc)
        return {}
    return {}


def _write_workspace_config_yaml(payload: dict[str, Any]) -> None:
    cfg_file = _workspace_config_path()
    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    cfg_file.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")


def detect_llm_providers() -> dict[str, Any]:
    host = str(os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")).strip()
    ollama_ready = False
    ollama_error = ""
    try:
        resp = requests.get(f"{host.rstrip('/')}/api/tags", timeout=2)
        ollama_ready = resp.status_code == 200
        if not ollama_ready:
            ollama_error = f"http_{resp.status_code}"
    except (requests.RequestException, OSError) as exc:
        ollama_error = str(exc)

    openai_key = str(os.environ.get("OPENAI_API_KEY", "")).strip()
    openrouter_key = str(os.environ.get("OPENROUTER_API_KEY", "")).strip()

    providers = {
        "ollama": {
            "enabled": True,
            "ready": bool(ollama_ready),
            "detail": ollama_error,
            "host": host,
        },
        "openai": {
            "enabled": True,
            "ready": bool(openai_key),
            "detail": "api_key_present" if openai_key else "missing_api_key",
        },
        "openrouter": {
            "enabled": True,
            "ready": bool(openrouter_key),
            "detail": "api_key_present" if openrouter_key else "missing_api_key",
        },
    }
    return providers


def suggest_llm_mode(providers: dict[str, Any] | None = None) -> dict[str, Any]:
    providers = providers or detect_llm_providers()
    ollama_ok = bool((providers.get("ollama") or {}).get("ready", False))
    openai_ok = bool((providers.get("openai") or {}).get("ready", False))
    openrouter_ok = bool((providers.get("openrouter") or {}).get("ready", False))
    cloud_ok = openai_ok or openrouter_ok

    if ollama_ok and cloud_ok:
        mode = "hybrid"
        chat_order = ["ollama", "openai", "openrouter"]
        embedding_order = ["ollama", "openai", "openrouter"]
    elif ollama_ok:
        mode = "local"
        chat_order = ["ollama", "openai", "openrouter"]
        embedding_order = ["ollama", "openai", "openrouter"]
    elif cloud_ok:
        mode = "cloud"
        chat_order = ["openai", "openrouter", "ollama"]
        embedding_order = ["openai", "openrouter", "ollama"]
    else:
        mode = "offline"
        chat_order = ["ollama", "openai", "openrouter"]
        embedding_order = ["ollama", "openai", "openrouter"]

    return {
        "mode": mode,
        "chat_order": chat_order,
        "embedding_order": embedding_order,
        "providers": providers,
        "available": mode != "offline",
    }


def derive_llm_ladder(providers: dict[str, Any]) -> dict[str, Any]:
    """Derive effective LLM capability ladder from provider readiness."""
    ollama_ok = bool((providers.get("ollama") or {}).get("ready", False))
    openai_ok = bool((providers.get("openai") or {}).get("ready", False))
    openrouter_ok = bool((providers.get("openrouter") or {}).get("ready", False))
    cloud_ok = openai_ok or openrouter_ok
    if ollama_ok and cloud_ok:
        mode = "full"
    elif ollama_ok and not cloud_ok:
        mode = "local_only"
    elif cloud_ok and not ollama_ok:
        mode = "cloud_only"
    else:
        mode = "rule_only"
    return {
        "mode": mode,
        "ollama_ready": ollama_ok,
        "cloud_ready": cloud_ok,
        "openai_ready": openai_ok,
        "openrouter_ready": openrouter_ok,
        "effective_available": mode != "rule_only",
    }


def configure_llm_mode_runtime(mode: str = "auto") -> dict[str, Any]:
    selected_mode = str(mode or "auto").strip().lower()
    if selected_mode not in {"auto", "local", "cloud", "hybrid"}:
        return {
            "ok": False,
            "error": "invalid_mode",
            "allowed_modes": ["auto", "local", "cloud", "hybrid"],
        }

    providers = detect_llm_providers()
    suggested = suggest_llm_mode(providers)
    target_mode = suggested["mode"] if selected_mode == "auto" else selected_mode

    if target_mode == "local":
        chat_order = ["ollama", "openai", "openrouter"]
        embedding_order = ["ollama", "openai", "openrouter"]
    elif target_mode == "cloud":
        chat_order = ["openai", "openrouter", "ollama"]
        embedding_order = ["openai", "openrouter", "ollama"]
    elif target_mode == "hybrid":
        chat_order = ["ollama", "openai", "openrouter"]
        embedding_order = ["ollama", "openai", "openrouter"]
    else:
        return {
            "ok": False,
            "error": "offline_unavailable",
            "message": "No Ollama service and no cloud API keys found.",
            "next_steps": [
                "Install and start Ollama, or",
                "Set OPENAI_API_KEY / OPENROUTER_API_KEY then rerun `ms8 llm setup --mode auto`.",
            ],
            "providers": providers,
        }

    cfg = _read_workspace_config_yaml()
    if not isinstance(cfg, dict):
        cfg = {}
    memory_cfg = cfg.get("memory")
    if not isinstance(memory_cfg, dict):
        memory_cfg = {}
        cfg["memory"] = memory_cfg
    llm_cfg_raw = memory_cfg.get("llm")
    if not isinstance(llm_cfg_raw, dict):
        llm_cfg: dict[str, Any] = {}
        memory_cfg["llm"] = llm_cfg
    else:
        llm_cfg = llm_cfg_raw

    llm_cfg["enabled"] = True
    llm_cfg["provider_order_chat"] = chat_order
    llm_cfg["provider_order_embedding"] = embedding_order

    openai_raw = llm_cfg.get("openai")
    openrouter_raw = llm_cfg.get("openrouter")
    openai_cfg: dict[str, Any] = openai_raw if isinstance(openai_raw, dict) else {}
    openrouter_cfg: dict[str, Any] = openrouter_raw if isinstance(openrouter_raw, dict) else {}

    openai_cfg["enabled"] = True
    openrouter_cfg["enabled"] = True

    llm_cfg["openai"] = openai_cfg
    llm_cfg["openrouter"] = openrouter_cfg

    _write_workspace_config_yaml(cfg)
    return {
        "ok": True,
        "requested_mode": selected_mode,
        "applied_mode": target_mode,
        "config_file": str(_workspace_config_path()),
        "provider_order_chat": chat_order,
        "provider_order_embedding": embedding_order,
        "providers": providers,
        "next_steps": [
            "Run `ms8 llm status` to verify active provider readiness.",
            "If using cloud providers, export OPENAI_API_KEY or OPENROUTER_API_KEY in your shell profile.",
        ],
    }


def get_llm_guide_runtime() -> dict[str, Any]:
    detected = suggest_llm_mode()
    return {
        "ok": True,
        "recommended_mode": detected.get("mode", "offline"),
        "providers": detected.get("providers", {}),
        "modes": {
            "local": {
                "when": "Prefer local privacy/offline inference",
                "command": "ms8 llm setup --mode local",
                "requirements": ["Ollama running on http://127.0.0.1:11434"],
            },
            "hybrid": {
                "when": "Prefer local first, fail over to cloud",
                "command": "ms8 llm setup --mode hybrid",
                "requirements": ["Ollama running", "OPENAI_API_KEY or OPENROUTER_API_KEY"],
            },
            "cloud": {
                "when": "No Ollama, use hosted LLM providers",
                "command": "ms8 llm setup --mode cloud",
                "requirements": ["OPENAI_API_KEY or OPENROUTER_API_KEY"],
            },
        },
        "env_examples": [
            "export OPENAI_API_KEY='sk-...'",
            "export OPENROUTER_API_KEY='sk-or-...'",
            "export OLLAMA_HOST='http://127.0.0.1:11434'",
        ],
    }


def get_llm_status_runtime() -> dict[str, Any]:
    engine_status = get_engine_llm_status()
    providers = detect_llm_providers()
    suggestion = suggest_llm_mode(providers)
    cfg = _read_workspace_config_yaml()
    memory_cfg = cfg.get("memory", {}) if isinstance(cfg.get("memory", {}), dict) else {}
    llm_cfg = memory_cfg.get("llm", {}) if isinstance(memory_cfg.get("llm", {}), dict) else {}

    ladder = derive_llm_ladder(providers)
    return {
        "ok": True,
        "engine": engine_status,
        "detected_providers": providers,
        "recommended_mode": suggestion.get("mode", "offline"),
        "effective_mode_ladder": ladder,
        "config_file": str(_workspace_config_path()),
        "configured": {
            "enabled": bool(llm_cfg.get("enabled", False)),
            "provider_order_chat": list(llm_cfg.get("provider_order_chat", [])),
            "provider_order_embedding": list(llm_cfg.get("provider_order_embedding", [])),
        },
        "effective_available": bool(ladder.get("effective_available", False)),
        "message": "Engine-only status may show disabled when runtime uses safe mode. Use detected_providers + configured for rollout.",
    }


def consume_llm_degraded_notice_runtime() -> dict[str, Any]:
    """
    Emit degraded LLM notice at most once per mode window to reduce noise.
    Returns: {"emit": bool, "mode": str, "message": str}
    """
    paths = ensure_runtime_dirs()
    state_file = paths["health"] / "llm_notice_state.json"
    status = get_llm_status_runtime()
    ladder = status.get("effective_mode_ladder", {}) if isinstance(status.get("effective_mode_ladder", {}), dict) else {}
    mode = str(ladder.get("mode", "rule_only"))
    emit = False
    message = ""
    prev_mode = ""
    if state_file.exists():
        try:
            payload = json.loads(state_file.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                prev_mode = str(payload.get("last_mode", ""))
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            logger.debug("Failed to parse llm notice state file: %s", exc)
            prev_mode = ""
    if mode == "rule_only" and prev_mode != "rule_only":
        emit = True
        message = (
            "LLM enhancement is currently in rule-only mode. "
            "Core memory still works; run `ms8 llm setup --mode local` or `ms8 llm setup --mode cloud` for semantic enhancement."
        )
    try:
        state_file.write_text(
            json.dumps({"last_mode": mode, "updated_at": _utc_now()}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.debug("Failed to persist llm notice state: %s", exc)
    return {"emit": emit, "mode": mode, "message": message}


def get_expression_router_status(*, sample_size: int = 200) -> dict[str, Any]:
    paths = ensure_runtime_dirs()
    reports_file = paths["root"] / "memory" / "reports" / "expression_router_decisions.jsonl"
    profile_file = paths["root"] / "memory" / "expression_preference_profile.json"
    state_file = paths["root"] / "memory" / "expression_router_state.json"

    status: dict[str, Any] = {
        "enabled": True,
        "decisions_file": str(reports_file),
        "sample_size": int(sample_size),
        "total_samples": 0,
        "mode_counts": {"normal": 0, "light": 0, "strong": 0},
        "strong_ratio": 0.0,
        "cooldown_applied_count": 0,
        "profile_used_count": 0,
        "top_reasons": [],
        "current_round": 0,
        "last_mode": None,
        "profile_evidence_count": 0,
    }

    if reports_file.exists():
        lines = reports_file.read_text(encoding="utf-8", errors="ignore").splitlines()
        tail = lines[-max(1, int(sample_size)) :]
        mode_counts = {"normal": 0, "light": 0, "strong": 0}
        cooldown_count = 0
        profile_used_count = 0
        reason_counter: Counter[str] = Counter()
        total = 0
        for ln in tail:
            raw = ln.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError as exc:
                logger.debug("Failed to parse expression router decision row: %s", exc)
                continue
            if not isinstance(obj, dict):
                continue
            mode = str(obj.get("mode", "")).strip().lower()
            if mode in mode_counts:
                mode_counts[mode] += 1
            if bool(obj.get("cooldown_applied", False)):
                cooldown_count += 1
            if bool(obj.get("profile_used", False)):
                profile_used_count += 1
            reason = str(obj.get("reason", "")).strip()
            if reason:
                reason_counter[reason] += 1
            total += 1
        status["total_samples"] = total
        status["mode_counts"] = mode_counts
        status["cooldown_applied_count"] = cooldown_count
        status["profile_used_count"] = profile_used_count
        status["strong_ratio"] = round((mode_counts["strong"] / max(1, total)), 4)
        status["top_reasons"] = [k for k, _ in reason_counter.most_common(3)]

    if state_file.exists():
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
            if isinstance(state, dict):
                status["current_round"] = int(state.get("current_round", 0) or 0)
                lm = state.get("last_mode")
                status["last_mode"] = lm if lm in {"normal", "light", "strong", None} else None
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.debug("Failed to read expression router state: %s", exc)

    if profile_file.exists():
        try:
            prof = json.loads(profile_file.read_text(encoding="utf-8"))
            if isinstance(prof, dict):
                status["profile_evidence_count"] = int(prof.get("evidence_count", 0) or 0)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.debug("Failed to read expression preference profile: %s", exc)

    return status


def get_capability_reachability_report(*, top_unreachable: int = 20) -> dict[str, Any]:
    """
    Estimate capability reachability by scanning MemoryCore public methods and
    checking whether names are referenced outside core.py.
    """
    core_path = Path(__file__).resolve().parent / "engine_core" / "core.py"
    if not core_path.exists():
        return {
            "status": "error",
            "reason": "core_file_missing",
            "core_path": str(core_path),
            "public_methods_total": 0,
            "referenced_methods": 0,
            "unreachable_methods": 0,
            "reachable_ratio": 0.0,
            "unreachable_top": [],
        }
    try:
        core_tree = ast.parse(core_path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError) as exc:
        return {
            "status": "error",
            "reason": f"ast_parse_failed:{exc}",
            "core_path": str(core_path),
            "public_methods_total": 0,
            "referenced_methods": 0,
            "unreachable_methods": 0,
            "reachable_ratio": 0.0,
            "unreachable_top": [],
        }

    public_methods: list[str] = []
    for node in core_tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "MemoryCore":
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and not item.name.startswith("_"):
                    public_methods.append(item.name)
            break

    base = Path(__file__).resolve().parent
    py_files = [p for p in base.rglob("*.py") if p != core_path]
    referenced = set()
    for p in py_files:
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            logger.debug("Failed to read file in capability reachability scan %s: %s", p, exc)
            continue
        # Static textual references (direct method call / attribute access).
        for m in public_methods:
            if m in referenced:
                continue
            if f"{m}(" in text or f".{m}" in text:
                referenced.add(m)
        # Dynamic dispatch references: _run_core_method("method_name", ...)
        try:
            parsed_tree = ast.parse(text)
        except SyntaxError as exc:
            logger.debug("Failed to parse AST in capability reachability scan %s: %s", p, exc)
            continue
        for ast_node in ast.walk(parsed_tree):
            if not isinstance(ast_node, ast.Call):
                continue
            func = ast_node.func
            if not isinstance(func, ast.Name) or func.id != "_run_core_method":
                continue
            if not ast_node.args:
                continue
            first = ast_node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                meth = first.value.strip()
                if meth in public_methods:
                    referenced.add(meth)

    total = len(public_methods)
    reachable = len(referenced)
    unreachable = sorted([m for m in public_methods if m not in referenced])
    ratio = round((reachable / total), 4) if total > 0 else 0.0
    return {
        "status": "success",
        "core_path": str(core_path),
        "public_methods_total": total,
        "referenced_methods": reachable,
        "unreachable_methods": len(unreachable),
        "reachable_ratio": ratio,
        "unreachable_top": unreachable[: max(1, int(top_unreachable))],
    }


def preview_rollback_auto_approved_synthetic(*, since_hours: int = 1) -> dict[str, Any]:
    engine = _engine()
    core = getattr(engine, "_core", None)
    if core is None or not hasattr(core, "preview_rollback_auto_approved_synthetic"):
        return {"status": "unsupported", "reason": "synthetic_rollback_preview_unavailable"}
    try:
        return core.preview_rollback_auto_approved_synthetic(since_hours=int(since_hours))
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.warning("runtime_preview_rollback_auto_approved_synthetic_failed err=%s", exc)
        return {"status": "error", "error": str(exc), "error_code": "E_RUNTIME_SYNTHETIC_ROLLBACK_PREVIEW_FAILED"}


def rollback_auto_approved_synthetic(*, since_hours: int = 1) -> dict[str, Any]:
    engine = _engine()
    core = getattr(engine, "_core", None)
    if core is None or not hasattr(core, "rollback_auto_approved_synthetic"):
        return {"status": "unsupported", "reason": "synthetic_rollback_unavailable"}
    try:
        return core.rollback_auto_approved_synthetic(since_hours=int(since_hours))
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.warning("runtime_rollback_auto_approved_synthetic_failed err=%s", exc)
        return {"status": "error", "error": str(exc), "error_code": "E_RUNTIME_SYNTHETIC_ROLLBACK_FAILED"}


def get_governance_report() -> dict[str, Any]:
    paths = ensure_runtime_dirs()
    rows = read_memories()
    total = len(rows)
    noncanonical = 0
    legacy_admission_records = 0
    revoked = 0
    superseded = 0
    pending = 0
    quarantined = 0
    ids: set[str] = set()
    dup_groups = 0
    hash_counter: dict[str, int] = {}
    for r in rows:
        status = str(r.get("status", "")).lower()
        if status == "revoked":
            revoked += 1
        elif status == "superseded":
            superseded += 1
        elif status == "pending_review":
            pending += 1
        elif status == "quarantined":
            quarantined += 1
        ok, _reason = validate_record(r) if isinstance(r, dict) else (False, "invalid_type")
        if not ok:
            noncanonical += 1
        admission = str((r.get("meta", {}) or {}).get("admission", "")).strip()
        if admission and not admission.startswith("ms8_write_guard"):
            legacy_admission_records += 1
        txt = str(r.get("normalized_text") or r.get("text") or "")
        h = txt.strip().lower()
        hash_counter[h] = hash_counter.get(h, 0) + 1
        rid = str(r.get("id", "")).strip()
        if rid:
            ids.add(rid)
    dup_groups = sum(1 for _, c in hash_counter.items() if c > 1)

    comp_report = paths["health"] / "compression_duplicate_cluster_latest.json"
    review_report = paths["health"] / "review_governance_latest.json"
    baseline_req = paths["root"] / "memory" / "reports" / "baseline_update_request.json"
    fallback_log = paths["health"] / "governance_fallback_log.jsonl"
    schema_invalid_total = 0
    schema_invalid_active = 0
    fallback_write_count = 0
    fallback_total_count = 0
    fallback_error_code_counter: Counter[str] = Counter()
    pending_oldest_hours = 0.0
    now = datetime.now(timezone.utc)
    active_window_days = int(os.environ.get("MS8_SCHEMA_INVALID_ACTIVE_DAYS", "14") or 14)
    active_window_s = max(1, active_window_days) * 86400
    if paths["quarantine"].exists():
        for ln in paths["quarantine"].read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                obj = json.loads(ln)
            except json.JSONDecodeError as exc:
                logger.debug("Failed to parse quarantine JSON row: %s", exc)
                continue
            if isinstance(obj, dict):
                schema_invalid_total += 1
                dt = _to_aware(str(obj.get("at") or ""))
                if dt is None:
                    schema_invalid_active += 1
                else:
                    age_s = max(0.0, (now - dt).total_seconds())
                    if age_s <= active_window_s:
                        schema_invalid_active += 1
    if fallback_log.exists():
        for ln in fallback_log.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                obj = json.loads(ln)
            except json.JSONDecodeError as exc:
                logger.debug("Failed to parse governance fallback log row: %s", exc)
                continue
            if not isinstance(obj, dict):
                continue
            fallback_total_count += 1
            reason = str(obj.get("reason", "") or "").strip().lower()
            error_code = str(obj.get("error_code", "") or "").strip() or _fallback_error_code_from_reason(reason)
            if error_code:
                fallback_error_code_counter[error_code] += 1
            if str(obj.get("kind", "")) == "write":
                fallback_write_count += 1
    if review_report.exists():
        try:
            rpt = json.loads(review_report.read_text(encoding="utf-8"))
            if isinstance(rpt, dict):
                summary = rpt.get("summary", {})
                if isinstance(summary, dict):
                    pending_oldest_hours = float(summary.get("pending_oldest_hours", 0.0) or 0.0)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.debug("Failed to parse review sync report: %s", exc)
            pending_oldest_hours = 0.0
    if pending == 0:
        # Guard against stale report snapshots carrying forward historical
        # "oldest_pending_hours" when there is no active pending item.
        pending_oldest_hours = 0.0
    self_check = run_engine_self_check(level="L4")
    self_check_status = str(self_check.get("status", "unknown")) if isinstance(self_check, dict) else "unknown"
    monitoring = get_engine_monitoring_status()
    rates = monitoring.get("rates", {}) if isinstance(monitoring, dict) and isinstance(monitoring.get("rates", {}), dict) else {}
    rates_v2 = (
        monitoring.get("rates_v2", {})
        if isinstance(monitoring, dict) and isinstance(monitoring.get("rates_v2", {}), dict)
        else {}
    )
    slo = monitoring.get("slo", {}) if isinstance(monitoring, dict) and isinstance(monitoring.get("slo", {}), dict) else {}
    slo_v2_preview = (
        monitoring.get("slo_v2_preview", {})
        if isinstance(monitoring, dict) and isinstance(monitoring.get("slo_v2_preview", {}), dict)
        else {}
    )
    compression_freshness = (
        monitoring.get("compression_freshness", {})
        if isinstance(monitoring, dict) and isinstance(monitoring.get("compression_freshness", {}), dict)
        else {}
    )
    compression_hours = compression_freshness.get("hours_since_last")
    compression_state_path = paths["compression_state"]
    compression_state = {}
    if compression_state_path.exists():
        try:
            compression_state = json.loads(compression_state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            logger.debug("Failed to read compression state JSON: %s", exc)
            compression_state = {}
    if not isinstance(compression_hours, (int, float)) and isinstance(compression_state, dict):
        ts_text = str(
            compression_state.get("last_success_at")
            or compression_state.get("last_run_at")
            or ""
        ).strip()
        dt = _to_aware(ts_text)
        if dt is not None:
            compression_hours = round(
                max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0),
                2,
            )

    capture_rate = float(rates.get("capture_rate", 0.0) or 0.0)
    auto_total_entries = int(rates.get("auto_total_entries", 0) or 0)
    capture_min = float((slo.get("targets", {}) or {}).get("capture_rate_min", 0.85) or 0.85)
    capture_min_samples = int((slo.get("targets", {}) or {}).get("capture_rate_min_samples", 30) or 30)
    capture_breach = auto_total_entries >= capture_min_samples and capture_rate < capture_min
    v2_all_ok = bool(slo_v2_preview.get("all_ok", False))
    v2_eligible_events = int(rates_v2.get("eligible_events", 0) or 0)

    config_file_payload: dict[str, Any] = {}
    try:
        cf = paths.get("config_file")
        if isinstance(cf, Path) and cf.exists():
            loaded = json.loads(cf.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                config_file_payload = loaded
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        logger.debug("Failed to load governance config JSON: %s", exc)
        config_file_payload = {}
    slo_cfg = config_file_payload.get("governance_slo", {}) if isinstance(config_file_payload.get("governance_slo", {}), dict) else {}
    authority = str(slo_cfg.get("authority", "v2_preview") or "v2_preview").strip().lower()
    if authority not in {"legacy_capture_rate", "v2_preview"}:
        authority = "legacy_capture_rate"
    v2_min_eligible_events = int(slo_cfg.get("v2_min_eligible_events", 30) or 30)
    v2_sample_ready = v2_eligible_events >= max(1, v2_min_eligible_events)

    compression_severity = "healthy"
    if isinstance(compression_hours, (int, float)):
        ch = float(compression_hours)
        if ch >= 168.0:
            compression_severity = "critical"
        elif ch >= 72.0:
            compression_severity = "degraded"
        elif ch >= 24.0:
            compression_severity = "warning"
    else:
        compression_severity = "warning"

    report: dict[str, Any] = {
        "at": _utc_now(),
        "total_records": total,
        "noncanonical_records": noncanonical,
        "legacy_admission_records": legacy_admission_records,
        "schema_invalid_count": schema_invalid_active,
        "schema_invalid_total_count": schema_invalid_total,
        "schema_invalid_active_window_days": active_window_days,
        "fallback_write_count": fallback_write_count,
        "fallback_total_count": fallback_total_count,
        "fallback_error_code_counts": dict(sorted(fallback_error_code_counter.items())),
        "fallback_error_code_top": [code for code, _ in fallback_error_code_counter.most_common(3)],
        "duplicate_groups": dup_groups,
        "pending_review": pending,
        "pending_review_oldest_hours": round(pending_oldest_hours, 3),
        "quarantined": quarantined,
        "revoked": revoked,
        "superseded": superseded,
        "self_check_status": self_check_status,
        "baseline_update_pending": baseline_req.exists(),
        "compression_cluster_report_exists": comp_report.exists(),
        "review_governance_report_exists": review_report.exists(),
        "compression_freshness_hours": compression_hours,
        "compression_severity": compression_severity,
        "capture_rate": capture_rate,
        "capture_rate_samples": auto_total_entries,
        "capture_rate_target": capture_min,
        "capture_rate_breach": bool(capture_breach),
        "rates_v2": rates_v2,
        "slo_v2_preview": slo_v2_preview,
        "slo_authority": authority,
        "slo_transition": {
            "current_authority": authority,
            "v2_preview_all_ok": v2_all_ok,
            "v2_sample_ready": v2_sample_ready,
            "v2_eligible_events": v2_eligible_events,
            "v2_min_eligible_events": v2_min_eligible_events,
            "ready_to_switch_v2": bool(v2_all_ok and v2_sample_ready),
        },
        "compression_state": compression_state if isinstance(compression_state, dict) else {},
    }
    # Layered governance health domains.
    runtime_health = "green"
    memory_quality_health = "green"
    retrieval_safety_health = "green"
    security_integrity_health = "green"
    lifecycle_maintenance_health = "green"
    overall_reasons: list[str] = []

    if self_check_status in {"fail", "error"}:
        runtime_health = "red"
        security_integrity_health = "red"
        overall_reasons.append("self_check_failed")
    elif self_check_status in {"warn", "warning"}:
        runtime_health = "yellow"
        security_integrity_health = "yellow"
        overall_reasons.append("self_check_warn")

    if authority == "v2_preview":
        # Primary memory-quality authority switched to v2 with sample gate.
        if not v2_sample_ready:
            memory_quality_health = "yellow"
            overall_reasons.append("v2_sample_not_ready")
        elif not v2_all_ok:
            memory_quality_health = "red"
            overall_reasons.append("v2_slo_not_ok")
        else:
            memory_quality_health = "green"
        # Legacy capture is caution only when v2 is authority.
        if capture_breach:
            overall_reasons.append("legacy_capture_caution")
    else:
        if capture_breach:
            memory_quality_health = "red"
            overall_reasons.append("legacy_capture_breach")
        elif auto_total_entries < capture_min_samples:
            memory_quality_health = "yellow"
            overall_reasons.append("legacy_capture_insufficient_samples")
        if not v2_all_ok and memory_quality_health == "green":
            memory_quality_health = "yellow"
            overall_reasons.append("v2_preview_caution")

    if compression_severity == "critical":
        lifecycle_maintenance_health = "red"
        overall_reasons.append("compression_critical")
    elif compression_severity in {"degraded", "warning"}:
        lifecycle_maintenance_health = "yellow"
        overall_reasons.append(f"compression_{compression_severity}")

    if fallback_write_count > 0 or schema_invalid_active > 0:
        retrieval_safety_health = "yellow"
        overall_reasons.append("retrieval_safety_alert")
    if fallback_write_count > 5 or schema_invalid_active > 0:
        retrieval_safety_health = "red"
        overall_reasons.append("retrieval_safety_high_risk")

    def _combine(*vals: str) -> str:
        if "red" in vals:
            return "red"
        if "yellow" in vals:
            return "yellow"
        return "green"

    overall = _combine(
        runtime_health,
        memory_quality_health,
        retrieval_safety_health,
        security_integrity_health,
        lifecycle_maintenance_health,
    )
    report["health_domains"] = {
        "runtime_health": runtime_health,
        "memory_quality_health": memory_quality_health,
        "retrieval_safety_health": retrieval_safety_health,
        "security_integrity_health": security_integrity_health,
        "lifecycle_maintenance_health": lifecycle_maintenance_health,
        "overall": overall,
        "overall_reasons": sorted(set(overall_reasons)),
    }
    report["health_domains"]["memory_quality_basis"] = {
        "legacy_capture_breach": bool(capture_breach),
        "v2_preview_all_ok": bool(v2_all_ok),
        "v2_sample_ready": bool(v2_sample_ready),
        "v2_eligible_events": v2_eligible_events,
        "v2_min_eligible_events": v2_min_eligible_events,
        "authority": authority,
    }
    _persist_governance_report(report=report, health_dir=paths["health"])
    report["trend"] = _governance_trend(health_dir=paths["health"])
    return report


def archive_schema_invalid_history(*, older_than_days: int = 30) -> dict[str, Any]:
    paths = ensure_runtime_dirs()
    qf = paths["quarantine"]
    if not qf.exists():
        return {"status": "skipped", "reason": "quarantine_missing", "archived": 0}
    now = datetime.now(timezone.utc)
    threshold_s = max(1, int(older_than_days or 30)) * 86400
    keep: list[str] = []
    archive_rows: list[str] = []
    for ln in qf.read_text(encoding="utf-8", errors="ignore").splitlines():
        raw = ln.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.debug("Failed to parse quarantine row while archiving: %s", exc)
            keep.append(raw)
            continue
        dt = _to_aware(str(obj.get("at") or ""))
        if dt is None:
            keep.append(raw)
            continue
        age_s = max(0.0, (now - dt).total_seconds())
        if age_s > threshold_s:
            archive_rows.append(json.dumps(obj, ensure_ascii=False))
        else:
            keep.append(raw)
    if archive_rows:
        stamp = now.strftime("%Y%m")
        ap = paths["health"] / "archive" / f"quarantine_schema_invalid_{stamp}.jsonl"
        ap.parent.mkdir(parents=True, exist_ok=True)
        prev = ap.read_text(encoding="utf-8", errors="ignore") if ap.exists() else ""
        ap.write_text(
            prev + ("\n" if prev and not prev.endswith("\n") else "") + "\n".join(archive_rows) + "\n",
            encoding="utf-8",
        )
    qf.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")
    return {
        "status": "success",
        "archived": len(archive_rows),
        "kept": len(keep),
        "older_than_days": int(older_than_days or 30),
    }


def repair_quarantine_records() -> dict[str, Any]:
    paths = ensure_runtime_dirs()
    qf = paths["quarantine"]
    mem = paths["memories"]
    if not qf.exists():
        return {"status": "skipped", "reason": "quarantine_missing", "repaired": 0}
    existing_ids: set[str] = set()
    if mem.exists():
        for ln in mem.read_text(encoding="utf-8", errors="ignore").splitlines():
            raw = ln.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError as exc:
                logger.debug("Failed to parse memory row while repairing quarantine: %s", exc)
                continue
            if isinstance(row, dict):
                rid = str(row.get("id", "")).strip()
                if rid:
                    existing_ids.add(rid)
    keep: list[str] = []
    repaired_rows: list[str] = []
    repaired = 0
    skipped = 0
    for ln in qf.read_text(encoding="utf-8", errors="ignore").splitlines():
        raw = ln.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.debug("Failed to parse quarantine row while repairing: %s", exc)
            keep.append(raw)
            skipped += 1
            continue
        rec = obj.get("record", {}) if isinstance(obj, dict) else {}
        if not isinstance(rec, dict):
            keep.append(raw)
            skipped += 1
            continue
        patched = dict(rec)
        txt = str(patched.get("text", ""))
        if "normalized_text" not in patched or not str(patched.get("normalized_text", "")).strip():
            patched["normalized_text"] = normalize_text(txt)
        if "category" not in patched or not str(patched.get("category", "")).strip():
            patched["category"] = "general"
        if "status" not in patched or not str(patched.get("status", "")).strip():
            patched["status"] = "candidate"
        if "meta" not in patched or not isinstance(patched.get("meta"), dict):
            patched["meta"] = {"admission": "ms8_quarantine_repair_v1"}
        else:
            patched["meta"].setdefault("admission", "ms8_quarantine_repair_v1")
        ok, _reason = validate_record(patched)
        rid = str(patched.get("id", "")).strip()
        if ok and rid and rid not in existing_ids:
            repaired_rows.append(json.dumps(patched, ensure_ascii=False))
            existing_ids.add(rid)
            repaired += 1
            continue
        keep.append(raw)
        skipped += 1
    if repaired_rows:
        with mem.open("a", encoding="utf-8") as f:
            f.write("\n".join(repaired_rows) + "\n")
    qf.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")
    return {
        "status": "success",
        "repaired": repaired,
        "skipped": skipped,
        "remaining_quarantine": len(keep),
    }


def _persist_governance_report(*, report: dict[str, Any], health_dir: Path) -> None:
    latest = health_dir / "governance_report_latest.json"
    history = health_dir / "governance_report_history.jsonl"
    try:
        latest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("Failed to persist governance latest report: %s", exc)
        return
    try:
        with history.open("a", encoding="utf-8") as f:
            f.write(json.dumps(report, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("Failed to append governance report history: %s", exc)
        return


def _governance_trend(*, health_dir: Path) -> dict[str, Any]:
    history = health_dir / "governance_report_history.jsonl"
    if not history.exists():
        return {"window_24h": {}, "window_7d": {}}

    now = datetime.now(timezone.utc)
    w24 = now.timestamp() - 24 * 3600
    w7d = now.timestamp() - 7 * 24 * 3600
    rows_24: list[dict[str, Any]] = []
    rows_7d: list[dict[str, Any]] = []

    for ln in history.read_text(encoding="utf-8", errors="ignore").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            obj = json.loads(ln)
        except json.JSONDecodeError as exc:
            logger.debug("Failed to parse governance history line: %s", exc)
            continue
        if not isinstance(obj, dict):
            continue
        at = str(obj.get("at") or "")
        try:
            raw = at[:-1] + "+00:00" if at.endswith("Z") else at
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            ts = dt.astimezone(timezone.utc).timestamp()
        except ValueError as exc:
            logger.debug("Failed to parse governance history timestamp '%s': %s", at, exc)
            continue
        if ts >= w24:
            rows_24.append(obj)
        if ts >= w7d:
            rows_7d.append(obj)

    return {
        "window_24h": _summarize_trend_window(rows_24),
        "window_7d": _summarize_trend_window(rows_7d),
    }


def _summarize_trend_window(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"samples": 0, "risk": "green"}
    first = rows[0]
    last = rows[-1]
    keys = (
        "noncanonical_records",
        "schema_invalid_count",
        "fallback_write_count",
        "fallback_total_count",
        "duplicate_groups",
        "pending_review",
    )
    delta = {}
    for k in keys:
        delta[k] = int(last.get(k, 0) or 0) - int(first.get(k, 0) or 0)
    first_codes = first.get("fallback_error_code_counts", {}) if isinstance(first, dict) else {}
    last_codes = last.get("fallback_error_code_counts", {}) if isinstance(last, dict) else {}
    spike_code = ""
    spike_value = 0
    code_deltas: dict[str, int] = {}
    if isinstance(first_codes, dict) and isinstance(last_codes, dict):
        all_codes = set(first_codes.keys()) | set(last_codes.keys())
        for code in all_codes:
            delta_code = int(last_codes.get(code, 0) or 0) - int(first_codes.get(code, 0) or 0)
            code_deltas[str(code)] = int(delta_code)
            delta[f"fallback_error_code__{code}"] = int(delta_code)
            if delta_code > spike_value:
                spike_value = delta_code
                spike_code = str(code)
    delta["fallback_error_code_spike"] = int(spike_value)
    risk = _risk_level_from_delta(delta, _load_governance_risk_config())
    return {
        "samples": len(rows),
        "delta": delta,
        "risk": risk,
        "latest_fallback_error_code_top": list(last.get("fallback_error_code_top", []) or []),
        "fallback_error_code_spike_code": spike_code,
        "fallback_error_code_spike_value": int(spike_value),
        "fallback_error_code_deltas": dict(sorted(code_deltas.items())),
    }


def _fallback_error_code_from_reason(reason: str) -> str:
    key = str(reason or "").strip().lower()
    mapping = {
        "core_unavailable": "E_CORE_UNAVAILABLE",
        "core_write_disabled": "E_CORE_WRITE_DISABLED",
        "core_write_timeout_or_error": "E_CORE_WRITE_TIMEOUT",
        "core_retrieval_disabled": "E_CORE_RETRIEVAL_DISABLED",
        "core_retrieval_timeout_or_error": "E_CORE_RETRIEVAL_TIMEOUT",
        "core_retrieval_no_results_fallback": "E_CORE_RETRIEVAL_EMPTY",
    }
    return mapping.get(key, "E_FALLBACK_GENERIC")


def _load_governance_risk_config() -> dict[str, Any]:
    paths = ensure_runtime_dirs()
    cfg_file = paths["config_file"]
    defaults = {
        "red": {
            "schema_invalid_count_gt": 0,
            "fallback_write_count_gt": 5,
            "fallback_total_count_gt": 12,
            "fallback_error_code_spike_gt": 6,
            "fallback_error_code_spike_gt_by_code": {"E_CORE_UNAVAILABLE": 3},
            "noncanonical_records_gt": 0,
        },
        "yellow": {
            "fallback_write_count_gt": 0,
            "fallback_total_count_gt": 3,
            "fallback_error_code_spike_gt": 2,
            "fallback_error_code_spike_gt_by_code": {"E_CORE_UNAVAILABLE": 1},
            "pending_review_gt": 5,
            "duplicate_groups_gt": 5,
        },
    }
    try:
        raw = json.loads(cfg_file.read_text(encoding="utf-8"))
        gov = raw.get("governance_risk", {}) if isinstance(raw, dict) else {}
        red = gov.get("red", {}) if isinstance(gov, dict) else {}
        yellow = gov.get("yellow", {}) if isinstance(gov, dict) else {}
        merged = {"red": dict(defaults["red"]), "yellow": dict(defaults["yellow"])}
        if isinstance(red, dict):
            merged["red"].update(
                {
                    k: int(v)
                    for k, v in red.items()
                    if isinstance(v, (int, float, str)) and str(v).strip().lstrip("-").isdigit()
                }
            )
            specific = red.get("fallback_error_code_spike_gt_by_code")
            if isinstance(specific, dict):
                merged["red"]["fallback_error_code_spike_gt_by_code"] = {
                    str(k): int(v)
                    for k, v in specific.items()
                    if isinstance(v, (int, float, str)) and str(v).strip().lstrip("-").isdigit()
                }
        if isinstance(yellow, dict):
            merged["yellow"].update(
                {
                    k: int(v)
                    for k, v in yellow.items()
                    if isinstance(v, (int, float, str)) and str(v).strip().lstrip("-").isdigit()
                }
            )
            specific = yellow.get("fallback_error_code_spike_gt_by_code")
            if isinstance(specific, dict):
                merged["yellow"]["fallback_error_code_spike_gt_by_code"] = {
                    str(k): int(v)
                    for k, v in specific.items()
                    if isinstance(v, (int, float, str)) and str(v).strip().lstrip("-").isdigit()
                }
        return merged
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.debug("Failed to load governance risk config; using defaults: %s", exc)
        return defaults


def update_governance_risk_config(
    *,
    red_schema_invalid_gt: int | None = None,
    red_fallback_write_gt: int | None = None,
    red_fallback_total_gt: int | None = None,
    red_fallback_error_code_spike_gt: int | None = None,
    red_noncanonical_gt: int | None = None,
    yellow_fallback_write_gt: int | None = None,
    yellow_fallback_total_gt: int | None = None,
    yellow_fallback_error_code_spike_gt: int | None = None,
    yellow_pending_review_gt: int | None = None,
    yellow_duplicate_groups_gt: int | None = None,
) -> dict[str, Any]:
    paths = ensure_runtime_dirs()
    cfg_file = paths["config_file"]
    current = _load_governance_risk_config()
    red = dict(current.get("red", {}))
    yellow = dict(current.get("yellow", {}))

    if red_schema_invalid_gt is not None:
        red["schema_invalid_count_gt"] = int(red_schema_invalid_gt)
    if red_fallback_write_gt is not None:
        red["fallback_write_count_gt"] = int(red_fallback_write_gt)
    if red_fallback_total_gt is not None:
        red["fallback_total_count_gt"] = int(red_fallback_total_gt)
    if red_fallback_error_code_spike_gt is not None:
        red["fallback_error_code_spike_gt"] = int(red_fallback_error_code_spike_gt)
    if red_noncanonical_gt is not None:
        red["noncanonical_records_gt"] = int(red_noncanonical_gt)
    if yellow_fallback_write_gt is not None:
        yellow["fallback_write_count_gt"] = int(yellow_fallback_write_gt)
    if yellow_fallback_total_gt is not None:
        yellow["fallback_total_count_gt"] = int(yellow_fallback_total_gt)
    if yellow_fallback_error_code_spike_gt is not None:
        yellow["fallback_error_code_spike_gt"] = int(yellow_fallback_error_code_spike_gt)
    if yellow_pending_review_gt is not None:
        yellow["pending_review_gt"] = int(yellow_pending_review_gt)
    if yellow_duplicate_groups_gt is not None:
        yellow["duplicate_groups_gt"] = int(yellow_duplicate_groups_gt)

    payload = {"governance_risk": {"red": red, "yellow": yellow}}
    cfg_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _risk_level_from_delta(delta: dict[str, int], cfg: dict[str, Any] | None = None) -> str:
    cfg = cfg or _load_governance_risk_config()
    schema_invalid = int(delta.get("schema_invalid_count", 0) or 0)
    fallback_write = int(delta.get("fallback_write_count", 0) or 0)
    fallback_total = int(delta.get("fallback_total_count", 0) or 0)
    fallback_spike = int(delta.get("fallback_error_code_spike", 0) or 0)
    noncanonical = int(delta.get("noncanonical_records", 0) or 0)
    pending = int(delta.get("pending_review", 0) or 0)
    dup = int(delta.get("duplicate_groups", 0) or 0)

    red_cfg = cfg.get("red", {}) if isinstance(cfg, dict) else {}
    yellow_cfg = cfg.get("yellow", {}) if isinstance(cfg, dict) else {}
    red_schema = int(red_cfg.get("schema_invalid_count_gt", 0))
    red_fallback = int(red_cfg.get("fallback_write_count_gt", 5))
    red_fallback_total = int(red_cfg.get("fallback_total_count_gt", 12))
    red_fallback_spike = int(red_cfg.get("fallback_error_code_spike_gt", 6))
    red_fallback_spike_by_code = (
        red_cfg.get("fallback_error_code_spike_gt_by_code", {}) if isinstance(red_cfg, dict) else {}
    )
    red_noncanonical = int(red_cfg.get("noncanonical_records_gt", 0))
    yellow_fallback = int(yellow_cfg.get("fallback_write_count_gt", 0))
    yellow_fallback_total = int(yellow_cfg.get("fallback_total_count_gt", 3))
    yellow_fallback_spike = int(yellow_cfg.get("fallback_error_code_spike_gt", 2))
    yellow_fallback_spike_by_code = (
        yellow_cfg.get("fallback_error_code_spike_gt_by_code", {}) if isinstance(yellow_cfg, dict) else {}
    )
    yellow_pending = int(yellow_cfg.get("pending_review_gt", 5))
    yellow_dup = int(yellow_cfg.get("duplicate_groups_gt", 5))

    env_red_fallback = os.environ.get("MS8_GOV_RISK_RED_FALLBACK_GT")
    env_red_fallback_total = os.environ.get("MS8_GOV_RISK_RED_FALLBACK_TOTAL_GT")
    env_yellow_pending = os.environ.get("MS8_GOV_RISK_YELLOW_PENDING_GT")
    env_yellow_fallback_total = os.environ.get("MS8_GOV_RISK_YELLOW_FALLBACK_TOTAL_GT")
    env_red_fallback_spike = os.environ.get("MS8_GOV_RISK_RED_FALLBACK_CODE_SPIKE_GT")
    env_yellow_fallback_spike = os.environ.get("MS8_GOV_RISK_YELLOW_FALLBACK_CODE_SPIKE_GT")
    if env_red_fallback and env_red_fallback.strip().isdigit():
        red_fallback = int(env_red_fallback.strip())
    if env_red_fallback_total and env_red_fallback_total.strip().isdigit():
        red_fallback_total = int(env_red_fallback_total.strip())
    if env_yellow_pending and env_yellow_pending.strip().isdigit():
        yellow_pending = int(env_yellow_pending.strip())
    if env_yellow_fallback_total and env_yellow_fallback_total.strip().isdigit():
        yellow_fallback_total = int(env_yellow_fallback_total.strip())
    if env_red_fallback_spike and env_red_fallback_spike.strip().isdigit():
        red_fallback_spike = int(env_red_fallback_spike.strip())
        red_fallback_spike_by_code = {}
    if env_yellow_fallback_spike and env_yellow_fallback_spike.strip().isdigit():
        yellow_fallback_spike = int(env_yellow_fallback_spike.strip())
        yellow_fallback_spike_by_code = {}

    red_specific_hit = False
    if isinstance(red_fallback_spike_by_code, dict):
        for code, threshold in red_fallback_spike_by_code.items():
            code_delta = int(delta.get(f"fallback_error_code__{code}", 0) or 0)
            if code_delta > int(threshold):
                red_specific_hit = True
                break
    yellow_specific_hit = False
    if isinstance(yellow_fallback_spike_by_code, dict):
        for code, threshold in yellow_fallback_spike_by_code.items():
            code_delta = int(delta.get(f"fallback_error_code__{code}", 0) or 0)
            if code_delta > int(threshold):
                yellow_specific_hit = True
                break

    if (
        schema_invalid > red_schema
        or fallback_write > red_fallback
        or fallback_total > red_fallback_total
        or fallback_spike > red_fallback_spike
        or red_specific_hit
        or noncanonical > red_noncanonical
    ):
        return "red"
    if (
        fallback_write > yellow_fallback
        or fallback_total > yellow_fallback_total
        or fallback_spike > yellow_fallback_spike
        or yellow_specific_hit
        or pending > yellow_pending
        or dup > yellow_dup
    ):
        return "yellow"
    return "green"
