"""
Core memory module interface - Enhanced with Letta-style features
"""

# Subdomain: engine (logical taxonomy; path unchanged)
import asyncio
import collections
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .auto_memory import AutoMemoryExtractor
from .built_in_skills import BuiltInSkills, SkillDiscovery
from .config import get_config
from .context_material import (
    assemble_shared_context_material,
    build_candidate_profiles,
    compute_dynamic_injection_budget,
    project_arbitration_context,
    project_injection_context,
    project_response_context,
)
from .context_understanding import ContextUnderstandingSystem
from .enhanced_self_improvement import EnhancedSelfImprovement
from .enhanced_subagents import SubAgentManager as EnhancedSubAgentManager
from .expression_preference_profile import (
    load_conversation_state,
    load_expression_profile,
    prepare_profile_for_round,
    resolve_expression_profile_dir,
    save_conversation_state,
    save_expression_profile,
    update_conversation_state_with_policy,
    update_profile_from_decision,
)
from .file_store import FileMemoryStore
from .file_write_guard import atomic_write_json, atomic_write_text
from .git_utils import GitMemoryManager
from .governance import MemoryGovernance
from .knowledge_arbitration import KnowledgeArbitrator
from .knowledge_feedback import KnowledgeFeedbackRecorder
from .knowledge_graph import KnowledgeGraph
from .learning import MemoryLearning
from .local_llm import LLMConfig
from .maintenance.self_check import load_latest_report, run_self_check
from .maintenance.self_repair import (
    build_repair_plan as build_self_repair_plan,
)
from .maintenance.self_repair import (
    list_repair_history,
    load_latest_repair_report,
)
from .maintenance.self_repair import (
    rollback_operation as rollback_self_repair_operation,
)
from .maintenance.self_repair import (
    run_repair_plan as run_self_repair_plan,
)
from .maintenance_manager import MaintenanceManager
from .maintenance_policy import build_policy_actions, gather_policy_stats
from .memory_blocks import MemoryBlocks
from .meta_cognition import MetaCognitionSystem
from .monitoring import MemoryMonitoring
from .pattern_recognition import PatternRecognition
from .record_gateway import append_memory_record, normalize_memory_records
from .response_mode_router import choose_cognitive_phrase, route_response
from .response_mode_types import RouterDecision
from .security import get_crypto_manager
from .security.recovery import recover_with_recovery_key
from .security.shadow import content_hash, get_shadow_system
from .self_improvement import ImprovementType, SelfImprovementEngine
from .semantic_search import SemanticMemorySearch
from .skill_github_discovery import GitHubSkillDiscovery
from .skill_marketplace import SkillInstaller, SkillRegistry
from .skill_search_index import SkillSearchIndex
from .skills import SkillManager
from .sqlite_store import SQLiteMemoryStore
from .sticky_prompt_templates import GUARDRAIL_PROMPT_EXTRA, build_profile_hint, get_prompt_extra
from .synthetic_memory import MemorySynthesizer
from .utils import ensure_memory_directories
from .whoosh_search import WhooshSearch
from .working_memory import WorkingMemoryManager

logger = logging.getLogger(__name__)


class MemoryCore:
    """
    Enhanced core memory management class with Letta-style features + Local LLM.

    New features:
    - Memory blocks (human/persona/archival)
    - /remember command for self-editing memory
    - Subagent delegation
    - Skill learning system
    - Local LLM integration (Ollama)
    - Smart routing & caching
    """

    def __init__(self, llm_enabled: bool = True, llm_config: LLMConfig | None = None):
        """
        Initialize MemoryCore with optional LLM integration.

        Args:
            llm_enabled: Whether to enable LLM features (default True)
            llm_config: LLM configuration (uses defaults if None)
        """
        self.config = get_config()
        self.fast_start = str(os.environ.get("OPENCLAW_MEMORY_FAST_START", "")).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self.file_store = FileMemoryStore()
        self.crypto = get_crypto_manager(self.config)
        self.shadow = get_shadow_system(self.config)
        # Delay import to avoid import-cycle fragility:
        # core -> app.pipeline -> app.memory.repository -> engine_core package.
        from ms8.app.pipeline import MemoryAdmissionEngine

        self.memory_admission = MemoryAdmissionEngine(Path(self.config["memory_dir"]) / "auto_memory_records.jsonl")
        self.sqlite_store = SQLiteMemoryStore()
        self.whoosh_search = WhooshSearch()
        self.semantic_search = SemanticMemorySearch()
        self.git_manager = GitMemoryManager()
        self.learning = None

        # Letta-style features
        self.memory_blocks = MemoryBlocks()
        self.governance = MemoryGovernance()
        self.subagents = EnhancedSubAgentManager(self)  # 使用增强版
        self.skills = SkillManager()

        # Advanced skill system
        self.skill_installer = SkillInstaller(self.skills)
        self.skill_registry = SkillRegistry()
        self.built_in_skills = BuiltInSkills()
        self.skill_discovery = SkillDiscovery(self.skills)

        # GitHub-based skill marketplace
        self.github_discovery = GitHubSkillDiscovery()
        self.skill_search_index = SkillSearchIndex()

        # LLM Integration
        self.llm_enabled = llm_enabled
        self.self_improvement: Any
        if llm_enabled:
            self.llm_config = llm_config or self._build_llm_config_from_settings()
            try:
                self.self_improvement = EnhancedSelfImprovement(self, self.llm_config)
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                logger.warning("LLM features disabled: %s", exc)
                self.llm_enabled = False
                self.self_improvement = SelfImprovementEngine(self)
        else:
            # Fallback to basic self-improvement
            self.self_improvement = SelfImprovementEngine(self)

        graph_llm = getattr(self.self_improvement, "llm", None) if self.llm_enabled else None
        self.knowledge_graph: KnowledgeGraph | None
        try:
            self.knowledge_graph = KnowledgeGraph(graph_llm, self.llm_config if self.llm_enabled else None)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning("Knowledge graph disabled: %s", exc)
            self.knowledge_graph = None

        self.learning = MemoryLearning(self.knowledge_graph, memory_core=self)

        # Initialize GitHub skill index in background to avoid blocking startup.
        self._sync_github_skills_async()

        # Initialize short-term memory
        max_size = self.config["settings"]["memory"]["short_term"]["max_size"]
        self.short_term_memory: collections.deque[str] = collections.deque(maxlen=max_size)

        # Working memory persistence / response injection
        self.working_memory = WorkingMemoryManager(self.config)

        # Long-term auto maintenance
        self.maintenance = MaintenanceManager(self.config, self.file_store)
        self._restore_short_term_memory()

        # Monitoring
        self.monitoring = MemoryMonitoring(self.config)
        self._maintenance_policy_state_file = self.config["memory_dir"] / "maintenance_policy_state.json"
        self._maintenance_policy_log_file = self.config["memory_dir"] / "maintenance_policy_log.jsonl"
        self._threshold_pending_file = self.config["memory_dir"] / "threshold_suggestions_pending.json"
        self._threshold_pending_key_file = self.config["memory_dir"] / "threshold_suggestions_pending.key"
        self._threshold_pending_key_source = "unknown"
        self._threshold_approval_log_file = self.config["memory_dir"] / "threshold_suggestions_approval_log.jsonl"
        self._write_fail_state_file = self.config["memory_dir"] / "write_fail_state.json"
        self._self_repair_state_file = self.config["memory_dir"] / "self_repair_state.json"
        self._write_fail_count = 0
        self._write_fail_recent: collections.deque[str] = collections.deque(maxlen=64)
        self._last_write_success_at = ""
        self._load_write_fail_state()
        self.knowledge_arbitrator = KnowledgeArbitrator(self.config)
        self.knowledge_feedback = KnowledgeFeedbackRecorder(self.config)
        self.context_understanding: ContextUnderstandingSystem | None = None
        self.pattern_recognition: PatternRecognition | None = None
        self._advanced_insight_count = 0
        self._recent_query_tokens: collections.deque[set[str]] = collections.deque(maxlen=24)
        self._init_advanced_insight_modules()

        # Automatic memory extraction
        self.auto_memory: AutoMemoryExtractor | None
        try:
            self.auto_memory = AutoMemoryExtractor(self)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning("Auto memory disabled: %s", exc)
            self.auto_memory = None

        # Memory synthesis (safe phase 1)
        self.synthesizer: MemorySynthesizer | None
        try:
            self.synthesizer = MemorySynthesizer(self)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning("Memory synthesizer disabled: %s", exc)
            self.synthesizer = None

        # Ensure directories exist
        ensure_memory_directories(self.config)

        # Meta-cognition
        self.meta_cognition = None
        meta_cfg = self.config["settings"]["memory"].get("meta_cognition", {})
        if meta_cfg.get("enabled", False):
            try:
                self.meta_cognition = MetaCognitionSystem(
                    getattr(self.self_improvement, "llm", None) if self.llm_enabled else None,
                    self.llm_config if self.llm_enabled else None,
                )
            except (OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
                logger.warning("Meta-cognition disabled: %s", exc)
                self.meta_cognition = None

        if not self.fast_start:
            # Initial maintenance pass (idempotent, interval-gated)
            try:
                self.maintenance.run_maintenance(force=False)
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                logger.warning("Maintenance bootstrap warning: %s", exc)
            try:
                self._run_maintenance_policy(force=False)
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                logger.warning("Maintenance policy bootstrap warning: %s", exc)

            # Pull real user turns from OpenClaw session logs into auto-memory.
            self._sync_openclaw_sessions(force=False)
            # Bootstrap one meta-cognition report when enabled and no history exists.
            if self.meta_cognition is not None:
                try:
                    if int(self.meta_cognition.get_status().get("report_count", 0)) == 0:
                        self.run_meta_cognition(period="daily")
                except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
                    logger.debug("Initial meta-cognition bootstrap skipped: %s", exc)
            self._write_config_audit_report()

        # Startup health-card integrity probe (log-only; no direct seal here).
        self._startup_health_card_probe()

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

    def _load_write_fail_state(self) -> None:
        try:
            if not self._write_fail_state_file.exists():
                return
            payload = json.loads(self._write_fail_state_file.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return
            self._write_fail_count = int(payload.get("consecutive_failures", 0) or 0)
            self._last_write_success_at = str(payload.get("last_write_success_at", "") or "")
            recent = payload.get("recent_failures", [])
            self._write_fail_recent.clear()
            if isinstance(recent, list):
                for ts in recent[-64:]:
                    s = str(ts or "").strip()
                    if s:
                        self._write_fail_recent.append(s)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            self._write_fail_count = 0
            self._write_fail_recent.clear()
            logger.warning("write_fail_state_load_failed; state reset to defaults: %s", exc)

    def _startup_health_card_probe(self) -> None:
        """
        Compare latest health card with current snapshot at startup.
        This probe is log-only by design to avoid conflicting with shadow control-plane sealing.
        """
        try:
            from .maintenance.self_check.reporter import _diff_health_card, build_health_card

            memory_dir = Path(self.config["memory_dir"])
            latest_path = memory_dir / "health_card_latest.json"
            if not latest_path.exists():
                return
            latest = json.loads(latest_path.read_text(encoding="utf-8"))
            if not isinstance(latest, dict):
                return
            current = build_health_card(self, snapshot_reason="startup_probe")
            diff = _diff_health_card(latest, current)
            summary = diff.get("summary", {}) if isinstance(diff.get("summary", {}), dict) else {}
            critical = int(summary.get("critical", 0) or 0)
            warning = int(summary.get("warning", 0) or 0)
            if critical > 0:
                logger.error("startup_integrity_critical: %s", summary)
            elif warning > 0:
                logger.warning("startup_integrity_warning: %s", summary)
            else:
                logger.info("startup_integrity_ok")
        except (ImportError, OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("startup_health_card_probe_error: %s", exc)

    def _persist_write_fail_state(self) -> None:
        recent_30s = 0
        now = self._utc_now()
        for ts_raw in list(self._write_fail_recent):
            raw = str(ts_raw).strip()
            if not raw:
                continue
            if raw.endswith("Z"):
                raw = raw[:-1] + "+00:00"
            try:
                ts = datetime.fromisoformat(raw)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                else:
                    ts = ts.astimezone(timezone.utc)
                if (now - ts).total_seconds() <= 30:
                    recent_30s += 1
            except ValueError as exc:
                logger.debug("write_fail_recent_parse_skipped raw=%s error=%s", raw, exc)
                continue
        payload = {
            "updated_at": now.isoformat(),
            "consecutive_failures": int(self._write_fail_count),
            "recent_failures_30s": int(recent_30s),
            "recent_failures": list(self._write_fail_recent),
            "last_write_success_at": self._last_write_success_at,
        }
        try:
            atomic_write_json(self._write_fail_state_file, payload, ensure_ascii=False, indent=2)
        except OSError as exc:
            logger.warning("write_fail_state_persist_failed: %s", exc)

    def _mark_write_success(self, source: str = "") -> None:
        self._write_fail_count = 0
        self._write_fail_recent.clear()
        self._last_write_success_at = self._utc_now().isoformat()
        self._persist_write_fail_state()
        if getattr(self, "shadow", None):
            try:
                self.shadow.record_data(
                    action="protect",
                    source="core.write_health",
                    content="write_success",
                    ok=True,
                    metadata={"source": str(source or "")},
                )
            except (RuntimeError, TypeError, ValueError) as exc:
                logger.debug("shadow_record_data_failed on write success: %s", exc)

    def _mark_write_failure(self, reason: str, source: str = "") -> None:
        immune_tokens = (
            "canary_probe",
            "self_check_latest",
            "self_check_history",
            "memory/reports/",
        )
        reason_s = str(reason or "")
        source_s = str(source or "")
        hay = f"{source_s} {reason_s}".lower()
        if any(tok in hay for tok in immune_tokens):
            return
        self._write_fail_count = int(self._write_fail_count) + 1
        self._write_fail_recent.append(self._utc_now().isoformat())
        self._persist_write_fail_state()
        if getattr(self, "shadow", None):
            try:
                self.shadow.record_data(
                    action="protect",
                    source="core.write_health",
                    content="write_failure",
                    ok=False,
                    error=str(reason or ""),
                    metadata={
                        "source": str(source or ""),
                        "consecutive_failures": int(self._write_fail_count),
                    },
                )
            except (RuntimeError, TypeError, ValueError) as exc:
                logger.debug("shadow_record_data_failed on write failure: %s", exc)

    def _init_advanced_insight_modules(self) -> None:
        """Best-effort activation for context/pattern analyzers (low-impact, interval gated)."""
        insight_cfg = self.config["settings"]["memory"].get("advanced_insight", {})
        if not bool(insight_cfg.get("enabled", True)):
            return
        llm = getattr(self.self_improvement, "llm", None)
        llm_config = getattr(self, "llm_config", None)
        try:
            if bool(insight_cfg.get("context_understanding_enabled", True)):
                self.context_understanding = ContextUnderstandingSystem(llm=llm, config=llm_config)
        except (RuntimeError, TypeError, ValueError) as exc:
            logger.warning("Context understanding disabled: %s", exc)
            self.context_understanding = None
        try:
            if bool(insight_cfg.get("pattern_recognition_enabled", True)):
                self.pattern_recognition = PatternRecognition(llm=llm, config=llm_config)
        except (RuntimeError, TypeError, ValueError) as exc:
            logger.warning("Pattern recognition disabled: %s", exc)
            self.pattern_recognition = None

    def _run_advanced_insight(self, latest_user_text: str) -> None:
        """Run lightweight context/pattern analysis on an interval to avoid per-turn overhead."""
        if not latest_user_text:
            return
        insight_cfg = self.config["settings"]["memory"].get("advanced_insight", {})
        if not bool(insight_cfg.get("enabled", True)):
            return
        self._advanced_insight_count += 1

        if self.context_understanding is not None:
            try:
                self.context_understanding.add_conversation("user", latest_user_text)
            except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
                logger.debug("context_understanding_add_conversation_failed: %s", exc)
        if self.pattern_recognition is not None:
            try:
                # Pattern module consumes conversation dicts directly.
                if not hasattr(self.pattern_recognition, "_conversation_history"):
                    self.pattern_recognition._conversation_history = []  # type: ignore[attr-defined]
                self.pattern_recognition._conversation_history.append(  # type: ignore[attr-defined]
                    {
                        "role": "user",
                        "content": latest_user_text,
                        "timestamp": self._utc_now().isoformat(),
                    }
                )
                self.pattern_recognition._conversation_history = self.pattern_recognition._conversation_history[-200:]  # type: ignore[attr-defined]
            except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
                logger.debug("pattern_recognition_history_append_failed: %s", exc)

        interval = max(1, int(insight_cfg.get("analyze_interval_interactions", 6)))
        min_turns = max(1, int(insight_cfg.get("min_turns_before_analyze", 4)))
        if self._advanced_insight_count < min_turns:
            return
        if self._advanced_insight_count % interval != 0:
            return

        max_hist = max(10, int(insight_cfg.get("max_history_items", 40)))
        conversations = [
            {"role": "user", "content": item}
            for item in list(self.short_term_memory)[-max_hist:]
            if isinstance(item, str) and item.strip()
        ]
        if len(conversations) < min_turns:
            return

        if self.context_understanding is not None:
            try:
                self._run_async(self.context_understanding.understand_context(conversations))
            except (RuntimeError, TypeError, ValueError) as exc:
                logger.warning("Context understanding runtime warning: %s", exc)
        if self.pattern_recognition is not None:
            try:
                self._run_async(
                    self.pattern_recognition.analyze_conversations(
                        conversations,
                        analyze_emotion=False,
                        analyze_patterns=True,
                    )
                )
            except (RuntimeError, TypeError, ValueError) as exc:
                logger.warning("Pattern recognition runtime warning: %s", exc)

    def _load_maintenance_policy_state(self) -> dict[str, Any]:
        if self._maintenance_policy_state_file.exists():
            try:
                obj = json.loads(self._maintenance_policy_state_file.read_text(encoding="utf-8"))
                if isinstance(obj, dict):
                    return obj
            except (OSError, json.JSONDecodeError, TypeError) as exc:
                logger.debug("maintenance_policy_state_load_failed; using default state: %s", exc)
        return {"last_runs": {}}

    def _save_maintenance_policy_state(self, state: dict[str, Any]) -> None:
        atomic_write_json(self._maintenance_policy_state_file, state, ensure_ascii=False, indent=2)

    def _append_maintenance_policy_log(self, payload: dict[str, Any]) -> None:
        self._maintenance_policy_log_file.parent.mkdir(parents=True, exist_ok=True)
        row = {"timestamp": self._utc_now().isoformat(), **payload}
        with self._maintenance_policy_log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _load_threshold_pending(self) -> dict[str, Any]:
        if self._threshold_pending_file.exists():
            try:
                obj = json.loads(self._threshold_pending_file.read_text(encoding="utf-8"))
                if isinstance(obj, dict):
                    items = obj.get("items", [])
                    obj["items"] = items if isinstance(items, list) else []
                    obj["_integrity_valid"] = self._verify_threshold_pending_signature(obj)
                    return obj
            except OSError as exc:
                logger.debug("threshold_pending_load_failed; using empty queue: %s", exc)
        return {
            "items": [],
            "last_generated_at": None,
            "last_applied_at": None,
            "_integrity_valid": True,
        }

    def _load_threshold_pending_hmac_key(self) -> bytes:
        path = self._threshold_pending_key_file
        security_cfg = self.config["settings"]["memory"].get("security", {})
        use_keychain = bool(security_cfg.get("use_keychain", False))
        if use_keychain:
            kc = self._load_threshold_pending_key_from_keychain()
            if kc:
                self._threshold_pending_key_source = "keychain"
                try:
                    if path.exists():
                        path.unlink(missing_ok=True)
                except OSError as exc:
                    logger.debug("threshold_key_file_cleanup_failed after keychain migration: %s", exc)
                return kc
        try:
            if path.exists():
                raw = path.read_bytes()
                if raw:
                    if use_keychain and self._save_threshold_pending_key_to_keychain(raw):
                        self._threshold_pending_key_source = "keychain"
                        try:
                            path.unlink(missing_ok=True)
                        except OSError as exc:
                            logger.debug("threshold_key_file_cleanup_failed after keychain hydration: %s", exc)
                        return raw
                    self._threshold_pending_key_source = "file"
                    return raw
        except OSError as exc:
            logger.debug("threshold_key_file_read_failed: %s", exc)
        key = secrets.token_bytes(32)
        if use_keychain and self._save_threshold_pending_key_to_keychain(key):
            self._threshold_pending_key_source = "keychain"
            return key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(key)
        try:
            os.chmod(path, 0o400)
        except (TypeError, ValueError) as exc:
            logger.debug("threshold_key_file_chmod_failed: %s", exc)
        self._threshold_pending_key_source = "file"
        return key

    def _threshold_pending_keychain_service(self) -> str:
        security_cfg = self.config["settings"]["memory"].get("security", {})
        base = str(security_cfg.get("keychain_service", "ms8-memory") or "ms8-memory")
        return f"{base}-threshold"

    def _threshold_pending_keychain_account(self) -> str:
        return "threshold-pending-hmac-key"

    def _load_threshold_pending_key_from_keychain(self) -> bytes | None:
        try:
            out = subprocess.run(
                [
                    "security",
                    "find-generic-password",
                    "-s",
                    self._threshold_pending_keychain_service(),
                    "-a",
                    self._threshold_pending_keychain_account(),
                    "-w",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=3,
            )
            if out.returncode != 0:
                return None
            val = str(out.stdout or "").strip()
            if not val:
                return None
            return bytes.fromhex(val)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            logger.debug("threshold_keychain_load_failed: %s", exc)
            return None

    def _save_threshold_pending_key_to_keychain(self, key: bytes) -> bool:
        try:
            payload = key.hex()
            out = subprocess.run(
                [
                    "security",
                    "add-generic-password",
                    "-U",
                    "-s",
                    self._threshold_pending_keychain_service(),
                    "-a",
                    self._threshold_pending_keychain_account(),
                    "-w",
                    payload,
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=3,
            )
            return out.returncode == 0
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug("threshold_keychain_save_failed: %s", exc)
            return False

    def _threshold_pending_signature(self, payload: dict[str, Any]) -> str:
        body = dict(payload or {})
        body.pop("_integrity", None)
        body.pop("_integrity_valid", None)
        canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        key = self._load_threshold_pending_hmac_key()
        return hmac.new(key, canonical.encode("utf-8"), hashlib.sha256).hexdigest()

    def _verify_threshold_pending_signature(self, payload: dict[str, Any]) -> bool:
        integ = payload.get("_integrity", {}) if isinstance(payload, dict) else {}
        if not isinstance(integ, dict):
            return False
        provided = str(integ.get("signature", "") or "")
        if not provided:
            return False
        expected = self._threshold_pending_signature(payload)
        return hmac.compare_digest(provided, expected)

    def _save_threshold_pending(self, payload: dict[str, Any]) -> None:
        body = dict(payload or {})
        body.pop("_integrity_valid", None)
        body["_integrity"] = {
            "algo": "hmac-sha256",
            "signature": self._threshold_pending_signature(body),
            "signed_at": self._utc_now().isoformat(),
        }
        self._threshold_pending_file.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self._threshold_pending_file, body, ensure_ascii=False, indent=2)

    def _append_threshold_approval_log(self, payload: dict[str, Any]) -> None:
        self._threshold_approval_log_file.parent.mkdir(parents=True, exist_ok=True)
        row = {"timestamp": self._utc_now().isoformat(), **payload}
        with self._threshold_approval_log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _queue_threshold_suggestions(self, report: dict[str, Any], source: str = "manual") -> dict[str, Any]:
        suggestions = report.get("suggestions", []) if isinstance(report, dict) else []
        if not isinstance(suggestions, list):
            suggestions = []
        pending = self._load_threshold_pending()
        approval_id = f"thr-{self._utc_now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"
        item = {
            "approval_id": approval_id,
            "created_at": self._utc_now().isoformat(),
            "status": "pending",
            "requires_confirmation": True,
            "source": source,
            "window": int(report.get("window", 0) or 0),
            "stats": report.get("stats", {}),
            "suggestions": suggestions,
            "report_file": str(self.config["workspace_dir"] / "memory" / "threshold_suggestions_weekly.json"),
        }
        items = pending.get("items", [])
        if not isinstance(items, list):
            items = []
        items.append(item)
        pending_max = int(
            self.config["settings"]["memory"].get("maintenance_policy", {}).get("threshold_suggestion_pending_max", 20)
            or 20
        )
        pending["items"] = items[-max(1, pending_max) :]
        pending["last_generated_at"] = self._utc_now().isoformat()
        self._save_threshold_pending(pending)
        return item

    def _read_workspace_yaml(self) -> dict[str, Any]:
        cfg_file = self.config["workspace_dir"] / "config.yaml"
        if not cfg_file.exists():
            return {}
        try:
            obj = yaml.safe_load(cfg_file.read_text(encoding="utf-8"))
            return obj if isinstance(obj, dict) else {}
        except (OSError, yaml.YAMLError, TypeError) as exc:
            logger.debug("workspace_yaml_read_failed file=%s error=%s", cfg_file, exc)
            return {}

    def _apply_threshold_suggestions_to_workspace_config(self, suggestions: list[dict[str, Any]]) -> dict[str, Any]:
        cfg_file = self.config["workspace_dir"] / "config.yaml"
        payload = self._read_workspace_yaml()
        if not isinstance(payload, dict):
            payload = {}
        mem = payload.get("memory")
        if not isinstance(mem, dict):
            mem = {}
            payload["memory"] = mem

        def _get_ref(root: dict[str, Any], parts: list[str]) -> Any:
            cur: Any = root
            for part in parts:
                if not isinstance(cur, dict) or part not in cur:
                    return None
                cur = cur[part]
            return cur

        def _set_ref(root: dict[str, Any], parts: list[str], value: Any) -> None:
            cur: Any = root
            for part in parts[:-1]:
                nxt = cur.get(part)
                if not isinstance(nxt, dict):
                    nxt = {}
                    cur[part] = nxt
                cur = nxt
            cur[parts[-1]] = value

        def _suggest_value(old: Any, item: dict[str, Any], key: str) -> Any:
            direction = str(item.get("direction", "")).lower()
            delta = item.get("delta", None)
            if isinstance(old, (int, float)) and isinstance(delta, (int, float)):
                new_val = float(old) + float(delta)
                if isinstance(old, int) and not isinstance(old, bool):
                    new_val = int(round(new_val))
                lname = key.lower()
                if any(token in lname for token in ["ratio", "threshold", "cap", "confidence", "score"]):
                    try:
                        new_val = max(0.0, min(1.0, float(new_val)))
                    except (TypeError, ValueError) as exc:
                        logger.debug("Threshold clamp skipped for key=%s: %s", key, exc)
                return new_val
            if isinstance(delta, (int, float)) and direction in {"increase", "decrease"}:
                base = 0.0
                if direction == "decrease":
                    base = 1.0
                return max(0.0, min(1.0, base + float(delta)))
            return old

        cfg_policy = self.config["settings"]["memory"].get("maintenance_policy", {})
        allowed_prefixes = cfg_policy.get(
            "threshold_suggestion_allowed_keys",
            [
                "working_memory.dynamic_injection_budget.",
                "knowledge_control.retrieval_mix_balancer.",
            ],
        )
        applied: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for sug in suggestions or []:
            if not isinstance(sug, dict):
                continue
            key = str(sug.get("key", "")).strip()
            if not key:
                continue
            if not re.match(r"^[a-zA-Z0-9_.-]+$", key):
                rejected.append({"key": key, "reason": "invalid_key_format"})
                continue
            if not any(key.startswith(str(prefix)) for prefix in (allowed_prefixes or [])):
                rejected.append({"key": key, "reason": "key_not_allowlisted"})
                continue
            parts = [x for x in key.split(".") if x]
            root = mem
            if parts and parts[0] == "memory":
                parts = parts[1:]
            if not parts:
                continue
            old = _get_ref(root, parts)
            if old is None:
                rejected.append({"key": key, "reason": "missing_existing_config_key"})
                continue
            new = _suggest_value(old, sug, key)
            if old == new:
                continue
            _set_ref(root, parts, new)
            applied.append(
                {
                    "key": key,
                    "old": old,
                    "new": new,
                    "direction": sug.get("direction"),
                    "delta": sug.get("delta"),
                }
            )

        if not applied:
            return {
                "status": "skipped",
                "reason": "no_effective_changes",
                "applied": [],
                "rejected": rejected,
            }

        text = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
        atomic_write_text(cfg_file, text, encoding="utf-8")
        return {
            "status": "success",
            "applied": applied,
            "rejected": rejected,
            "config_file": str(cfg_file),
        }

    def _policy_action_due(self, state: dict[str, Any], action: str, cooldown_hours: int) -> bool:
        last = str((state.get("last_runs") or {}).get(action, ""))
        if not last:
            return True
        try:
            raw = str(last).strip()
            if raw.endswith("Z"):
                raw = raw[:-1] + "+00:00"
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
            return (self._utc_now() - dt).total_seconds() >= max(1, cooldown_hours) * 3600
        except ValueError as exc:
            logger.debug("policy_action_due_parse_failed action=%s raw=%s error=%s", action, last, exc)
            return True

    def _run_maintenance_policy(self, force: bool = False) -> dict[str, Any]:
        cfg = self.config["settings"]["memory"].get("maintenance_policy", {})
        if not bool(cfg.get("enabled", True)):
            return {"status": "disabled"}
        stats = gather_policy_stats(self.config["workspace_dir"], cfg)
        actions = build_policy_actions(stats)
        state = self._load_maintenance_policy_state()
        cooldowns = dict(cfg.get("cooldown_hours", {}))
        ran = []
        skipped = []
        for action in actions:
            cd = int(cooldowns.get(action.action, 24))
            if not force and not self._policy_action_due(state, action.action, cd):
                skipped.append({"action": action.action, "reason": "cooldown"})
                continue
            result: dict[str, Any] = {"status": "skipped", "reason": "unknown_action"}
            if action.action == "trigger_weekly_compression":
                result = self.trigger_weekly_compression(confirm=True)
            elif action.action == "purge_test_memory_data":
                result = self.purge_test_memory_data()
            elif action.action == "cleanup_test_memories":
                result = self.purge_test_memory_data()
            elif action.action == "backfill_auto_memory_record_ids":
                result = self.backfill_auto_memory_record_ids()
            elif action.action == "shadow_replay_spool":
                # Respect encryption lock state when protected files are unreadable.
                if self.crypto.is_enabled() and (not self.crypto.is_unlocked()):
                    result = {"status": "blocked", "reason": "memory_security_locked"}
                else:
                    result = self.shadow_replay_spool()
            elif action.action == "shadow_auto_replay":
                if self.crypto.is_enabled() and (not self.crypto.is_unlocked()):
                    result = {"status": "blocked", "reason": "memory_security_locked"}
                else:
                    result = self.shadow_replay_spool()
            elif action.action == "shadow_auto_recover":
                if self.crypto.is_enabled() and (not self.crypto.is_unlocked()):
                    result = {"status": "blocked", "reason": "memory_security_locked"}
                else:
                    result = self.shadow_recover_from_events()
            elif action.action == "shadow_auto_seal":
                reason = f"auto_write_fail_streak:{int(stats.get('write_fail_consecutive', 0) or 0)}"
                result = self.shadow_seal(reason=reason, level="hard")
            elif action.action == "shadow_reset_checkpoint":
                result = self.shadow_reset_checkpoint()
            elif action.action == "shadow_recovery_drill":
                result = self.shadow_recovery_drill()
            elif action.action == "shadow_archive_spool":
                result = self.shadow_archive_spool()
            elif action.action == "shadow_sync_verified_backup":
                result = self.shadow_sync_verified_backup()
            elif action.action == "shadow_startup_self_heal":
                result = self.shadow_startup_self_heal()
            elif action.action == "repair_semantic_cache":
                result = self.repair_semantic_cache(limit=int(cfg.get("semantic_repair_limit", 30)))
            elif action.action == "rebalance_feedback_distribution":
                result = self.rebalance_feedback_distribution(window=int(cfg.get("feedback_rebalance_window", 200)))
            elif action.action == "generate_threshold_suggestions":
                result = self.generate_threshold_suggestions(
                    window=int(cfg.get("feedback_rebalance_window", 200)),
                    enqueue_for_approval=bool(cfg.get("require_threshold_suggestion_approval", True)),
                    source="maintenance_policy",
                )
            elif action.action == "auto_navigate_threshold_suggestions":
                result = self.auto_navigate_threshold_suggestions(
                    limit=int(cfg.get("threshold_auto_navigate_batch_limit", 1) or 1)
                )
            elif action.action == "trigger_batch_extract_kg":
                kg_limit = int(cfg.get("kg_batch_extract_limit", 20) or 20)
                result = self.batch_extract_knowledge_graph(limit=kg_limit, force=False)
            elif action.action == "trigger_memory_tiering":
                result = self.trigger_memory_tiering()
            elif action.action == "self_check_l1":
                result = self.run_self_check(level="L1")
            elif action.action == "self_check_l2l3":
                result = self.run_self_check(level="FULL")
            elif action.action == "self_check_l4":
                result = self.run_self_check(level="L4")
            elif action.action == "self_repair_auto":
                # Conservative default: auto-trigger generates + executes dry-run plan only.
                result = self.run_self_repair(mode="dry-run", auto=True)
            elif action.action == "trigger_batch_review":
                review_mode = str(cfg.get("auto_review_mode", "triage_default"))
                review_limit = int(cfg.get("auto_review_batch_limit", 30))
                accept_min = float(cfg.get("auto_review_accept_conf_min", 0.62))
                reject_max = float(cfg.get("auto_review_reject_conf_max", 0.20))
                per_cat_limit = int(cfg.get("auto_review_per_category_limit", 6))
                result = self.batch_review(
                    mode=review_mode,
                    limit=review_limit,
                    accept_conf_min=accept_min,
                    reject_conf_max=reject_max,
                    per_category_limit=per_cat_limit,
                )
                # Backlog fallback: if first pass did nothing, run one conservative reject-only pass
                # with a slightly looser reject threshold to avoid persistent queue stagnation.
                backlog = int(stats.get("review_backlog_pending", 0) or 0)
                th_cfg = cfg.get("thresholds", {}) or {}
                backlog_th = int(th_cfg.get("review_backlog_pending_threshold", 80))
                backlog_soft = int(th_cfg.get("review_backlog_pending_soft_threshold", 50))
                if int(result.get("reviewed", 0) or 0) == 0 and backlog >= min(backlog_th, backlog_soft):
                    fallback_reject_max = float(cfg.get("auto_review_reject_conf_max_backlog", 0.35))
                    fallback_drain_conf = float(cfg.get("auto_review_drain_reject_conf_max", 0.50))
                    fallback = self.batch_review(
                        mode="drain_backlog",
                        limit=max(5, review_limit // 2),
                        accept_conf_min=accept_min,
                        reject_conf_max=fallback_reject_max,
                        per_category_limit=per_cat_limit,
                        drain_reject_conf_max=fallback_drain_conf,
                    )
                    result = {
                        **result,
                        "fallback_run": True,
                        "fallback_reject_conf_max": fallback_reject_max,
                        "fallback_result": fallback,
                    }
            state.setdefault("last_runs", {})[action.action] = self._utc_now().isoformat()
            ran.append({"action": action.action, "reason": action.reason, "result": result})
            self._append_maintenance_policy_log(
                {"action": action.action, "trigger_reason": action.reason, "result": result}
            )
        self._save_maintenance_policy_state(state)
        return {"status": "success", "stats": stats, "ran": ran, "skipped": skipped}

    def run_self_check(self, level: str = "L1") -> dict[str, Any]:
        """Run maintenance self-check suite and return latest report payload."""
        try:
            return run_self_check(self, level=level)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return {
                "status": "error",
                "level": str(level or "L1").upper(),
                "error": str(exc),
            }

    def get_self_check_report(self) -> dict[str, Any]:
        """Read latest self-check report without running checks."""
        try:
            return load_latest_report(self.config)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return {"status": "error", "error": str(exc)}

    def run_self_repair(
        self,
        mode: str = "dry-run",
        *,
        domain: str = "",
        check_id: str = "",
        risk: str = "",
        approve_r3: bool = False,
        auto: bool = False,
    ) -> dict[str, Any]:
        """
        Run maintenance self-repair pipeline.

        mode:
          - dry-run (default): plan + estimate only
          - apply: execute repair actions
        """
        try:
            m = str(mode or "dry-run").strip().lower()
            if m not in {"dry-run", "apply"}:
                m = "dry-run"
            # Safety gate: R3 requires explicit manual apply unless enabled by config.
            if m == "apply":
                cfg_sc = self.config.get("settings", {}).get("memory", {}).get("self_check", {})
                allow_r3_auto = bool(cfg_sc.get("allow_r3_auto_apply", False))
                if auto and (not allow_r3_auto):
                    risk = "R1"
            plan = build_self_repair_plan(
                self,
                mode=m,
                only_risk=str(risk or ""),
                domain=str(domain or ""),
                check_id=str(check_id or ""),
            )
            if m == "dry-run":
                return plan
            plan["r3_approved"] = bool(approve_r3)
            plan["auto"] = bool(auto)
            return run_self_repair_plan(self, plan, mode=m)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return {
                "status": "error",
                "mode": str(mode or "dry-run"),
                "error": str(exc),
            }

    def get_self_repair_report(self) -> dict[str, Any]:
        try:
            return load_latest_repair_report(self.config["memory_dir"])
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return {"status": "error", "error": str(exc)}

    def get_self_repair_history(self, limit: int = 10) -> dict[str, Any]:
        try:
            rows = list_repair_history(self.config["memory_dir"], limit=max(1, int(limit)))
            return {"status": "ok", "rows": rows}
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return {"status": "error", "error": str(exc)}

    def rollback_self_repair_operation(self, operation_id: str) -> dict[str, Any]:
        try:
            return rollback_self_repair_operation(self, str(operation_id or ""))
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return {"status": "error", "error": str(exc), "operation_id": str(operation_id or "")}

    def _run_async(self, value):
        """Run coroutine values from sync APIs."""
        if not asyncio.iscoroutine(value):
            return value

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(value)

        result_holder = {"result": None, "error": None}

        def runner() -> None:
            try:
                result_holder["result"] = asyncio.run(value)
            except RuntimeError as exc:
                result_holder["error"] = exc

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        thread.join()
        if result_holder["error"] is not None:
            raise result_holder["error"]
        return result_holder["result"]

    def _evaluate_admission(self, text: str, source: str = "core_write") -> dict[str, Any]:
        try:
            from .admission_compat import evaluate_candidate

            decision = evaluate_candidate(str(text or ""), metadata={"source": source})
            return decision.to_dict()
        except (ImportError, RuntimeError, TypeError, ValueError) as exc:
            # Fail-open for backward compatibility, but preserve observability.
            return {
                "normalized_text": str(text or "").strip(),
                "route": "accepted",
                "reasons": [f"admission_fallback:{exc}"],
                "privacy_flags": [],
                "conflict_flags": [],
                "risk_scores": {},
                "should_persist_main": True,
                "should_index": True,
                "should_write_memory_md": True,
                "redacted": False,
                "replace_old": False,
                "raw": {},
            }

    def _safe_text_for_memory_md(self, text: str) -> dict[str, Any]:
        admission = self._evaluate_admission(text, source="memory_md")
        if not bool(admission.get("should_write_memory_md", False)):
            return {
                "allowed": False,
                "route": admission.get("route", "rejected"),
                "reasons": admission.get("reasons", []),
                "text": "",
                "admission": admission,
            }
        return {
            "allowed": True,
            "route": admission.get("route", "accepted"),
            "reasons": admission.get("reasons", []),
            "text": str(admission.get("normalized_text", text)).strip(),
            "admission": admission,
        }

    def _write_config_audit_report(self) -> None:
        """Emit a lightweight audit snapshot of config coverage for tunable parameters."""
        cfg = self.config["settings"]["memory"]
        audit_cfg = cfg.get("config_audit", {})
        if not bool(audit_cfg.get("enabled", True)):
            return
        report_raw = str(audit_cfg.get("report_file", "memory/config_audit_report.json"))
        report_file = Path(report_raw).expanduser()
        if not report_file.is_absolute():
            report_file = self.config["workspace_dir"] / report_file
        report_file.parent.mkdir(parents=True, exist_ok=True)

        coverage = {
            "retrieval_fusion": bool(cfg.get("retrieval_fusion")),
            "working_memory_ranking_weights": bool(cfg.get("working_memory", {}).get("ranking_weights")),
            "governance_trust_scoring": bool(cfg.get("governance", {}).get("trust_scoring")),
            "self_improvement_scoring": bool(cfg.get("self_improvement_scoring")),
            "meta_cognition_thresholds": bool(cfg.get("meta_cognition_thresholds")),
            "knowledge_graph_quality": bool(cfg.get("knowledge_graph_quality")),
        }
        remaining_hotspots = [
            "knowledge_graph: relation extraction regex pattern base scores are still mostly static",
            "meta_cognition: several fallback base scores remain hardcoded",
            "self_improvement: test heuristic constants (length/token bonuses) remain hardcoded",
            "app/pipeline: some thresholds still embedded in module defaults",
        ]
        payload = {
            "timestamp": self._utc_now().isoformat(),
            "coverage": coverage,
            "coverage_ratio": round(sum(1 for _, v in coverage.items() if v) / max(1, len(coverage)), 4),
            "remaining_hotspots": remaining_hotspots,
        }
        try:
            atomic_write_json(report_file, payload, ensure_ascii=False, indent=2)
        except OSError as exc:
            logger.warning("Config audit warning: %s", exc)

    def _append_context_snapshot(self, payload: dict[str, Any]) -> None:
        wm_cfg = self.config["settings"]["memory"].get("working_memory", {})
        raw = str(wm_cfg.get("context_snapshot_log_file", "memory/context_snapshots.jsonl"))
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = self.config["workspace_dir"] / path
        path.parent.mkdir(parents=True, exist_ok=True)
        line = {**payload, "timestamp": self._utc_now().isoformat()}
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")

    def _query_tokens(self, text: str) -> set[str]:
        tokens = {m.group(0).lower() for m in re.finditer(r"[A-Za-z0-9_\-]+|[\u4e00-\u9fff]{2,}", text or "")}
        zh = "".join(re.findall(r"[\u4e00-\u9fff]", text or ""))
        if len(zh) >= 2:
            for i in range(0, len(zh) - 1):
                tokens.add(zh[i : i + 2])
        return tokens

    def _recent_topic_consistency_score(self, message: str, window: int = 6) -> float:
        cur = self._query_tokens(message)
        if not cur:
            return 0.0
        if not hasattr(self, "_recent_query_tokens"):
            self._recent_query_tokens = collections.deque(maxlen=24)
        history = list(self._recent_query_tokens)[-max(1, window) :]
        if not history:
            return 0.0
        sims: list[float] = []
        for prev in history:
            if not prev:
                continue
            sims.append(len(cur & prev) / max(1, len(cur | prev)))
        if not sims:
            return 0.0
        sims.sort(reverse=True)
        top = sims[: min(3, len(sims))]
        return round(sum(top) / max(1, len(top)), 4)

    def _compute_profile_assist_score(self, profile: dict[str, Any], query_type: str, cfg: dict[str, Any]) -> float:
        weights = cfg.get("context_signal_weight_by_query_type", {})
        base_w = 0.0
        if isinstance(weights, dict):
            raw_w = weights.get(query_type, weights.get("default", 0.06))
            if isinstance(raw_w, (int, float, str)):
                try:
                    base_w = float(raw_w)
                except (TypeError, ValueError):
                    base_w = 0.06
            else:
                base_w = 0.06
        if base_w <= 0:
            base_w = 0.06
        topic_match = float(profile.get("topic_match", 0.0) or 0.0)
        coverage = float(profile.get("query_coverage", 0.0) or 0.0)
        pron = float(profile.get("pronoun_resolution_confidence", 0.0) or 0.0)
        cross = 1.0 if bool(profile.get("cross_turn_dependency", False)) else 0.0
        signal = 0.45 * topic_match + 0.35 * coverage + 0.15 * pron + 0.05 * cross
        cap = float(cfg.get("context_signal_assist_cap", 0.18))
        return round(min(cap, base_w * signal), 4)

    def _get_context_assist_signals(self, message: str) -> dict[str, Any]:
        """Thin integration: read context-understanding hints without changing core retrieval path."""
        signals = {
            "intent_type": "question" if ("?" in message or "？" in message) else "statement",
            "emotional_mode": "neutral",
            "time_reference": "none",
            "cross_turn_dependency": False,
            "pronoun_resolution_confidence": 0.0,
        }
        lowered = message.lower()
        if any(x in lowered for x in ("昨天", "上周", "之前", "earlier", "before", "last ")):
            signals["time_reference"] = "past"
        elif any(x in lowered for x in ("明天", "下周", "之后", "later", "next ")):
            signals["time_reference"] = "future"
        elif any(x in lowered for x in ("今天", "现在", "当前", "now", "today")):
            signals["time_reference"] = "present"

        pronouns = ("这个", "那个", "这些", "它", "他", "她", "this", "that", "it")
        if any(p in lowered for p in pronouns):
            signals["cross_turn_dependency"] = True
            signals["pronoun_resolution_confidence"] = 0.35
        switch_cues = ("换个", "新话题", "另一个问题", "不相关", "切换", "by the way", "anyway")
        has_switch_cue = any(c in lowered for c in switch_cues)
        if has_switch_cue:
            signals["cross_turn_dependency"] = False
            signals["pronoun_resolution_confidence"] = 0.0

        if self.context_understanding is not None:
            try:
                history = list(getattr(self.context_understanding, "understandings", {}).values())
                if history:
                    latest = max(history, key=lambda x: x.timestamp)
                    signals["intent_type"] = str(getattr(latest.user_intent, "value", signals["intent_type"]))
                    if latest.emotional_state:
                        signals["emotional_mode"] = str(latest.emotional_state)
                    if getattr(latest, "references", None) and not has_switch_cue:
                        refs = list(latest.references)
                        if refs:
                            avg = sum(float(r.get("confidence", 0.8) or 0.8) for r in refs) / max(1, len(refs))
                            signals["pronoun_resolution_confidence"] = round(avg, 4)
                            signals["cross_turn_dependency"] = True
                    if getattr(latest, "cross_time_links", None) and not has_switch_cue:
                        if len(list(latest.cross_time_links)) > 0:
                            signals["cross_turn_dependency"] = True
            except (AttributeError, TypeError, ValueError) as exc:
                logger.debug("Context-understanding assist signals unavailable: %s", exc)
        if self.pattern_recognition is not None:
            try:
                patterns = list(getattr(self.pattern_recognition, "patterns", {}).values())
                if patterns:
                    latest = sorted(patterns, key=lambda p: getattr(p, "last_seen", datetime.min), reverse=True)[:3]
                    names = " ".join(str(getattr(p, "name", "")) for p in latest).lower()
                    if any(k in names for k in ("high_interaction_frequency", "config_decision_coupling")):
                        signals["cross_turn_dependency"] = True
                        signals["pronoun_resolution_confidence"] = max(
                            float(signals.get("pronoun_resolution_confidence", 0.0)),
                            0.45,
                        )
            except (AttributeError, TypeError, ValueError) as exc:
                logger.debug("Pattern-recognition assist signals unavailable: %s", exc)
        return signals

    def _graph_enabled(self) -> bool:
        return bool(self.knowledge_graph and self.knowledge_graph.is_enabled())

    def _build_memory_ref(self, source: str, title: str, content: str) -> str:
        digest = hashlib.sha1(f"{source}|{title}|{content}".encode()).hexdigest()[:12]
        title_key = re.sub(r"[^a-zA-Z0-9_-]+", "-", (title or source).strip()).strip("-").lower() or "entry"
        return f"{source}::{title_key}::{digest}"

    def _retrieval_fingerprint(self, source: str, title: str, content: str) -> str:
        norm = " ".join(str(content or "").lower().split())
        if norm:
            # content-first dedupe for multi-source retrieval fusion
            return f"content::{norm[:220]}"
        return f"meta::{str(source or '').lower()}::{str(title or '').lower()}"

    def _query_domain(self, query: str) -> str:
        q = str(query or "").lower()
        rf_cfg = self.config["settings"]["memory"].get("retrieval_fusion", {})
        prior_cfg = rf_cfg.get("query_intent_source_prior", {})
        kws = [str(x).lower() for x in prior_cfg.get("governance_keywords", [])]
        if any(k and k in q for k in kws):
            return "governance"
        if any(
            k in q
            for k in [
                "配置",
                "阈值",
                "review",
                "policy",
                "maintenance",
                "queue",
                "backlog",
                "监控",
                "告警",
            ]
        ):
            return "governance"
        return "general"

    def _parse_any_datetime(self, value: Any) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)
        try:
            raw = str(value).strip()
            if raw.endswith("Z"):
                raw = raw[:-1] + "+00:00"
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except (TypeError, ValueError) as exc:
            logger.debug("parse_any_datetime_failed value=%s error=%s", value, exc)
            return None

    def _source_prior_adjust(self, row: dict[str, Any], query: str) -> tuple[float, str]:
        rf_cfg = self.config["settings"]["memory"].get("retrieval_fusion", {})
        prior_cfg = rf_cfg.get("query_intent_source_prior", {})
        if not bool(prior_cfg.get("enabled", True)):
            return 0.0, ""

        domain = self._query_domain(query)
        if domain != "governance":
            return 0.0, ""

        source = str(row.get("source", "")).lower()
        title = str(row.get("title", "")).lower()
        content = str(row.get("content", "")).lower()
        blob = f"{source} {title} {content}"

        boost = 0.0
        reasons: list[str] = []

        # Prefer live conversational sources for operational/governance queries.
        for pref in [str(x).lower() for x in prior_cfg.get("prefer_source_prefixes", [])]:
            if pref and source.startswith(pref):
                boost += 0.08
                reasons.append(f"src:{pref}")
                break

        if source == "memory.md":
            boost += float(prior_cfg.get("memory_md_bonus", 0.06))
            reasons.append("memory_md")

        title_kws = [str(x).lower() for x in prior_cfg.get("governance_title_boost_keywords", [])]
        if any(k and k in blob for k in title_kws):
            boost += 0.06
            reasons.append("gov_kw")

        narrative_kws = [str(x).lower() for x in prior_cfg.get("narrative_penalty_keywords", [])]
        if any(k and k in blob for k in narrative_kws):
            boost -= 0.14
            reasons.append("narrative_penalty")

        dt = self._parse_any_datetime(row.get("date"))
        if dt is not None:
            age_days = (self._utc_now() - dt).total_seconds() / 86400.0
            if age_days <= float(prior_cfg.get("recent_days_threshold", 14)):
                boost += float(prior_cfg.get("recent_days_bonus", 0.04))
                reasons.append("recent_bonus")
            elif age_days >= float(prior_cfg.get("stale_days_threshold", 45)):
                boost += float(prior_cfg.get("stale_days_penalty", -0.05))
                reasons.append("stale_penalty")

        max_boost = float(prior_cfg.get("max_boost", 0.18))
        min_penalty = float(prior_cfg.get("min_penalty", -0.22))
        boost = max(min_penalty, min(max_boost, boost))
        return round(boost, 4), ",".join(reasons)

    def _dispatch_knowledge_graph_ingest(
        self, source: str, title: str, content: str, use_llm: bool | None = None
    ) -> None:
        if not self._graph_enabled() or not content.strip():
            return
        kg = self.knowledge_graph
        if kg is None:
            return

        memory_ref = self._build_memory_ref(source, title, content)

        def runner() -> None:
            try:
                kg.ingest_memory(
                    memory_ref=memory_ref,
                    content=content,
                    source=source,
                    title=title,
                    use_llm=use_llm,
                )
            except (OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
                logger.warning("Knowledge graph ingest error: %s", exc)

        threading.Thread(target=runner, daemon=True).start()

    def _dispatch_graph_batch_extract(self, force: bool = False) -> None:
        if not self._graph_enabled():
            return
        kg = self.knowledge_graph
        if kg is None:
            return

        def runner() -> None:
            try:
                kg.batch_extract_pending_memories(force=force)
            except (OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
                logger.warning("Knowledge graph batch extract error: %s", exc)

        threading.Thread(target=runner, daemon=True).start()

    def _sync_openclaw_sessions(self, force: bool = False) -> dict[str, Any]:
        if not getattr(self, "auto_memory", None):
            return {"status": "skipped", "reason": "auto_memory_unavailable"}
        if not hasattr(self.auto_memory, "sync_openclaw_sessions"):
            return {"status": "skipped", "reason": "session_sync_unavailable"}
        auto = self.auto_memory
        if auto is None:
            return {"status": "skipped", "reason": "auto_memory_unavailable"}
        try:
            return auto.sync_openclaw_sessions(force=force)
        except (OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
            logger.warning("OpenClaw session sync warning: %s", exc)
            return {"status": "error", "error": str(exc)}

    def _restore_short_term_memory(self) -> None:
        cfg = self.config["settings"]["memory"].get("short_term", {})
        if not cfg.get("persist_enabled", True):
            return
        try:
            max_size = self.short_term_memory.maxlen or int(cfg.get("max_size", 200) or 200)
            restored = self.working_memory.restore_items(max_size=max_size)
            for item in restored:
                if item:
                    self.short_term_memory.append(item)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning("Short-term restore warning: %s", exc)

    def _estimate_importance(self, text: str) -> float:
        wm_cfg = self.config["settings"]["memory"].get("working_memory", {})
        keywords = wm_cfg.get("high_importance_keywords", [])
        imp_cfg = wm_cfg.get("importance_estimation", {})
        score = float(imp_cfg.get("base_score", 0.45))
        keyword_hit_bonus = float(imp_cfg.get("keyword_hit_bonus", 0.08))
        long_text_threshold = int(imp_cfg.get("long_text_threshold", 180))
        long_text_bonus = float(imp_cfg.get("long_text_bonus", 0.08))
        punctuation_bonus = float(imp_cfg.get("punctuation_bonus", 0.05))
        lowered = text.lower()
        for token in keywords:
            if token.lower() in lowered:
                score += keyword_hit_bonus
        if len(text) > long_text_threshold:
            score += long_text_bonus
        if "!" in text or "？" in text or "?" in text:
            score += punctuation_bonus
        return min(1.0, round(score, 3))

    def _infer_topic(self, text: str) -> str:
        candidates = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,6}", text or "")
        stop = {"the", "and", "for", "with", "this", "that", "我们", "这个", "那个"}
        for c in candidates:
            if c.lower() not in stop:
                return c
        return "general"

    def _memory_md_fallback_hits(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        fusion_cfg = self.config["settings"]["memory"].get("retrieval_fusion", {})
        fallback_score = float(fusion_cfg.get("memory_md_fallback_base_score", 0.45))
        tokens = set(re.findall(r"[A-Za-z0-9_\-]+|[\u4e00-\u9fff]", query or ""))
        if not tokens:
            return []
        text = self.file_store.read_memory_md()
        hits: list[dict[str, Any]] = []
        for line in text.splitlines():
            snippet = line.strip()
            if not snippet or snippet.startswith("#"):
                continue
            overlap = sum(1 for t in tokens if t in snippet)
            if overlap <= 0:
                continue
            hits.append(
                {
                    "source": "MEMORY.md",
                    "title": "Fallback",
                    "content": snippet[:220],
                    "score": float(overlap) + fallback_score,
                }
            )
        hits.sort(key=lambda x: x.get("score", 0.0), reverse=True)
        return hits[:limit]

    def _expression_router_config(self) -> dict[str, Any]:
        settings = self.config.get("settings", {}) if isinstance(self.config, dict) else {}
        memory = settings.get("memory", {}) if isinstance(settings, dict) else {}
        router = memory.get("expression_router", {}) if isinstance(memory, dict) else {}
        return router if isinstance(router, dict) else {}

    def _log_expression_decision(self, memory_dir: Path, decision: RouterDecision, current_round: int) -> None:
        logs = memory_dir / "reports" / "expression_router_decisions.jsonl"
        logs.parent.mkdir(parents=True, exist_ok=True)
        payload = decision.to_dict()
        payload["current_round"] = int(current_round)
        payload["timestamp"] = datetime.now(timezone.utc).isoformat()
        with logs.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _build_expression_mode_context(self, message: str, context_payload: dict[str, Any]) -> dict[str, Any]:
        memory_dir = resolve_expression_profile_dir(Path(self.config["memory_dir"]))
        recent_summary = str(context_payload.get("context", "") or context_payload.get("shared_context", "") or "")
        try:
            state = load_conversation_state(memory_dir)
            profile = load_expression_profile(memory_dir)
            current_round = int(state.current_round) + 1
            router_cfg = self._expression_router_config()
            profile_cfg = router_cfg.get("profile", {}) if isinstance(router_cfg.get("profile", {}), dict) else {}
            decay = float(profile_cfg.get("decay", 0.95))
            prepared_profile, valid_profile = prepare_profile_for_round(
                profile,
                current_round=current_round,
                decay=decay,
            )
            decision = route_response(
                user_message=message,
                recent_summary=recent_summary,
                profile=prepared_profile if valid_profile else None,
                conversation_state=state,
                router_config=router_cfg,
            )
        except (
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
            AttributeError,
            json.JSONDecodeError,
        ) as exc:
            logger.warning("expression_router_failed err=%s", exc)
            decision = RouterDecision(reason="router_fallback_normal")
            state = None
            prepared_profile = None
            valid_profile = False

        prompt_extra = get_prompt_extra(decision.mode)
        selected_phrase = choose_cognitive_phrase(
            decision.mode,
            state.last_cognitive_phrase if state is not None else None,
        )
        if decision.profile_used and valid_profile and prepared_profile is not None:
            hint = build_profile_hint(prepared_profile)
            prompt_extra = f"{prompt_extra}\n\n{hint}".strip() if prompt_extra else hint
        if state is not None and state.last_cognitive_phrase and decision.mode in {"light", "strong"}:
            avoid_line = f"表达约束：尽量不要复用上一轮认知转向句式“{state.last_cognitive_phrase}”。"
            prompt_extra = f"{prompt_extra}\n\n{avoid_line}".strip() if prompt_extra else avoid_line
        if selected_phrase:
            phrase_line = f"本轮可优先使用认知转向句式：{selected_phrase}（如不自然可不用）"
            prompt_extra = f"{prompt_extra}\n\n{phrase_line}".strip() if prompt_extra else phrase_line
        if GUARDRAIL_PROMPT_EXTRA:
            prompt_extra = (
                f"{prompt_extra}\n\n{GUARDRAIL_PROMPT_EXTRA}".strip()
                if prompt_extra
                else GUARDRAIL_PROMPT_EXTRA
            )

        logged_round = 0
        try:
            if state is not None and prepared_profile is not None:
                cooldown_cfg = self._expression_router_config().get("cooldown", {})
                reset_rounds = int(cooldown_cfg.get("reset_rounds_without_strong", 3) or 3)
                next_state = update_conversation_state_with_policy(
                    state,
                    decision,
                    reset_rounds_without_strong=reset_rounds,
                )
                if selected_phrase:
                    next_state.last_cognitive_phrase = selected_phrase
                save_conversation_state(memory_dir, next_state)
                next_profile = update_profile_from_decision(
                    prepared_profile,
                    decision,
                    current_round=next_state.current_round,
                )
                save_expression_profile(memory_dir, next_profile)
                logged_round = int(next_state.current_round)
            elif state is not None:
                logged_round = int(state.current_round)
            self._log_expression_decision(memory_dir, decision, logged_round)
        except (OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
            logger.warning("expression_profile_update_failed err=%s", exc)

        return {
            "mode": decision.mode,
            "confidence": decision.confidence,
            "prompt_extra": prompt_extra,
            "decision": decision.to_dict(),
        }

    def get_response_memory_context(self, message: str, top_k: int | None = None) -> dict[str, Any]:
        # Best-effort sync before retrieval, so responses can use latest dialogs.
        self._sync_openclaw_sessions(force=False)

        wm_cfg = self.config["settings"]["memory"].get("working_memory", {})
        base_top_k = top_k or int(wm_cfg.get("injection_top_k", 5))
        base_max_chars = int(wm_cfg.get("max_injection_chars", 1800))
        dyn_budget_cfg = wm_cfg.get("dynamic_injection_budget", {})
        context_assist_signals = self._get_context_assist_signals(message)
        recent_topic_consistency = self._recent_topic_consistency_score(
            message,
            window=int(dyn_budget_cfg.get("topic_consistency_window", 6)),
        )
        force_injection_enabled = bool(wm_cfg.get("force_injection_enabled", True))
        force_injection_min_items = int(wm_cfg.get("force_injection_min_items", 2))

        errors: list[str] = []
        candidates: list[dict[str, Any]] = []
        try:
            unified = self.retrieve_memories(
                message, top_k=max(10, base_top_k * 3), allow_semantic=True, allow_graph=True
            )
            candidates = [
                {
                    "id": row.get("id", ""),
                    "content": row.get("content", ""),
                    "source": row.get("source", ""),
                    "date": row.get("date", ""),
                    "title": row.get("title", ""),
                    "score": row.get("scores", {}).get("fusion", 0.0),
                    "rerank_score": row.get("scores", {}).get("rerank", 0.0),
                    "governance": row.get("raw", {}).get("governance", {}),
                    "knowledge_tier": row.get("knowledge_tier", "observation"),
                    "trust_level": row.get("trust_level", "hypothesis"),
                    "usage_permission": row.get(
                        "usage_permission", {"recall": True, "inject": "weak", "speak": "hint"}
                    ),
                }
                for row in unified
            ]
        except (OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
            errors.append(str(exc))
            unified = []

        ranked_all = (
            self.working_memory.rank_search_results(message, candidates, top_k=max(12, base_top_k * 4))
            if candidates
            else []
        )
        topic_hits = self.working_memory.restore_by_topic(message, limit=min(3, max(3, base_top_k)))
        candidate_profiles, batch_profile = build_candidate_profiles(
            message,
            ranked_all,
            topic_hits=topic_hits,
            context_signals=context_assist_signals,
        )
        if bool(dyn_budget_cfg.get("enabled", True)):
            budget = compute_dynamic_injection_budget(
                message,
                candidate_profiles,
                base_top_k=base_top_k,
                base_max_chars=base_max_chars,
                state={
                    "topic_hit_count": len(topic_hits),
                    "context_assist": context_assist_signals,
                    "recent_topic_consistency": recent_topic_consistency,
                },
                cfg=dyn_budget_cfg,
            )
        else:
            budget = {
                "query_type": "direct",
                "complexity_level": "simple",
                "complexity_score": 0.0,
                "budget_top_k": base_top_k,
                "budget_chars": base_max_chars,
                "low_trust_cap_count": max(1, int(round(base_top_k * 0.3))),
                "high_trust_ratio": 0.0,
                "low_trust_ratio": 0.0,
                "topic_state": "continue",
            }
        top_k = int(budget.get("budget_top_k", base_top_k))
        budget_chars = int(budget.get("budget_chars", base_max_chars))
        low_cap_count = int(budget.get("low_trust_cap_count", max(1, int(top_k * 0.3))))
        profile_by_id = {str(p.get("id", "")): p for p in candidate_profiles if str(p.get("id", ""))}
        query_type = str(budget.get("query_type", "direct"))
        for row in ranked_all:
            pid = str(row.get("id", ""))
            p = profile_by_id.get(pid, {})
            row["assist_score"] = (
                self._compute_profile_assist_score(p, query_type=query_type, cfg=dyn_budget_cfg) if p else 0.0
            )
        ranked = ranked_all
        blocked_injection = 0
        if ranked:
            # injection control + speaking-right control
            allowed = [x for x in ranked if str((x.get("usage_permission") or {}).get("inject", "none")) != "none"]
            blocked_injection = max(0, len(ranked) - len(allowed))
            speak_priority = {"primary": 3, "support": 2, "hint": 1, "deny": 0}
            allowed.sort(
                key=lambda x: (
                    speak_priority.get(str((x.get("usage_permission") or {}).get("speak", "hint")), 1),
                    float(profile_by_id.get(str(x.get("id", "")), {}).get("topic_match", 0.0) or 0.0),
                    float(x.get("assist_score", 0.0) or 0.0),
                    float(x.get("working_rank", x.get("score", 0.0)) or 0.0),
                ),
                reverse=True,
            )
            topic_state = str(budget.get("topic_state", "continue"))
            if topic_state == "hard_switch":
                allowed = [
                    x
                    for x in allowed
                    if float(profile_by_id.get(str(x.get("id", "")), {}).get("topic_match", 0.0) or 0.0) >= 0.08
                    or float(profile_by_id.get(str(x.get("id", "")), {}).get("query_coverage", 0.0) or 0.0) >= 0.12
                ]
            high = [x for x in allowed if str(x.get("trust_level", "hypothesis")) in {"hard_trust", "soft_trust"}]
            low = [x for x in allowed if str(x.get("trust_level", "hypothesis")) in {"hypothesis", "isolated"}]
            selected: list[dict[str, Any]] = []
            for row in high:
                if len(selected) >= top_k:
                    break
                selected.append(row)
            low_used = 0
            for row in low:
                if len(selected) >= top_k:
                    break
                if low_used >= low_cap_count:
                    break
                selected.append(row)
                low_used += 1
            if len(selected) < top_k:
                for row in allowed:
                    if len(selected) >= top_k:
                        break
                    if row in selected:
                        continue
                    selected.append(row)
            ranked = selected[:top_k]
        if not ranked:
            ranked = self._memory_md_fallback_hits(message, limit=top_k)

        shared_material = assemble_shared_context_material(
            message,
            latest_memories=[{"content": x} for x in list(self.short_term_memory)[-10:]],
            retrieval_candidates=candidates,
            topic_hits=topic_hits,
            state={
                "injection_top_k": top_k,
                "unified_count": len(unified),
                "topic_hit_count": len(topic_hits),
            },
            candidate_profiles=candidate_profiles,
            batch_profile=batch_profile,
            budget=budget,
        )
        response_projection = project_response_context(shared_material)
        injection_projection = project_injection_context(shared_material)
        arbitration_projection = project_arbitration_context(shared_material)

        sections: list[str] = []
        decision_reasons: list[str] = []
        fallback_usage_items: list[dict[str, Any]] = []
        injected = self.working_memory.build_injection_text(ranked, heading="Relevant Memories", max_chars=budget_chars)
        injection_reason = "ranked_memories"
        if injected:
            sections.append(injected)
            decision_reasons.append("primary_ranked_injection")

        if not injected and topic_hits:
            fallback_lines = ["## Relevant Memories"]
            for idx, item in enumerate(topic_hits, start=1):
                snippet = str(item.get("content", "")).replace("\n", " ")[:160]
                fallback_lines.append(f"{idx}. [working_memory] {snippet}")
            sections.append("\n".join(fallback_lines))
            injection_reason = "topic_fallback"
            decision_reasons.append("topic_fallback_used")

        if not injected and not topic_hits and force_injection_enabled:
            fallback = self._memory_md_fallback_hits(message, limit=max(force_injection_min_items, top_k))
            if fallback:
                forced_lines = ["## Relevant Memories"]
                for idx, item in enumerate(fallback[: max(force_injection_min_items, top_k)], start=1):
                    snippet = str(item.get("content", "")).replace("\n", " ")[:160]
                    forced_lines.append(f"{idx}. [{item.get('source', 'MEMORY.md')}] {snippet}")
                sections.append("\n".join(forced_lines))
                injection_reason = "forced_fallback_injection"
                fallback_usage_items = [
                    {
                        "id": str(item.get("id", "")),
                        "source": item.get("source", "MEMORY.md"),
                        "title": item.get("title", "Fallback"),
                        "score": item.get("score", 0.0),
                        "trust_level": "hypothesis",
                        "knowledge_tier": "observation",
                    }
                    for item in fallback[: max(force_injection_min_items, top_k)]
                ]
                decision_reasons.append("forced_memory_md_fallback_used")
            else:
                injection_reason = "no_candidates_forced_injection_miss"
                decision_reasons.append("forced_fallback_miss")

        if topic_hits:
            decision_reasons.append("session_continuity_included")
            lines = ["## Session Continuity"]
            for idx, item in enumerate(topic_hits, start=1):
                snippet = str(item.get("content", "")).replace("\n", " ")[:140]
                lines.append(f"{idx}. [{item.get('topic', 'general')}] {snippet}")
            sections.append("\n".join(lines))

        synthetic_bundle = self._get_synthetic_context_bundle(limit=2)
        if synthetic_bundle.get("text"):
            decision_reasons.append("synthetic_hint_included")
            sections.append(str(synthetic_bundle.get("text")))
            try:
                if self.synthesizer and synthetic_bundle.get("candidate_ids"):
                    self.synthesizer.record_candidate_hits(
                        list(synthetic_bundle.get("candidate_ids", [])),
                        used=True,
                        rebuttal=False,
                    )
            except (OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
                logger.debug("synthetic_candidate_hit_record_failed: %s", exc)

        usage_items: list[dict[str, Any]]
        if ranked:
            usage_items = ranked
        elif topic_hits:
            usage_items = [
                {
                    "source": "working_memory",
                    "title": "topic_restore",
                    "score": i.get("score", 0.0),
                    "trust_level": "soft_trust",
                    "knowledge_tier": "short_term",
                }
                for i in topic_hits
            ]
        elif fallback_usage_items:
            usage_items = fallback_usage_items
        else:
            usage_items = []
        self.working_memory.log_usage(
            message,
            usage_items,
            channel="response_context",
            reason=injection_reason if sections else "no_injection",
            candidates_count=len(candidates),
            topic_hits_count=len(topic_hits),
        )
        # feedback-control hook: record retrieval hit/waste and answer usage evidence.
        for item in candidates[: max(top_k * 2, 6)]:
            usage = item.get("usage_permission", {})
            used = any(str(x.get("id", "")) == str(item.get("id", "")) for x in usage_items if isinstance(x, dict))
            trust_level = str(item.get("trust_level", "hypothesis"))
            promotion_events = 1 if used and trust_level in {"soft_trust", "hard_trust"} else 0
            demotion_events = 1 if (not used) and trust_level in {"soft_trust", "hard_trust"} else 0
            self.knowledge_feedback.record_usage(
                knowledge_id=str(item.get("id", ""))
                or self._build_memory_ref(
                    str(item.get("source", "")),
                    str(item.get("title", "")),
                    str(item.get("content", "")),
                ),
                tier=str(item.get("knowledge_tier", "observation")),
                trust=trust_level,
                retrieval_hits=1 if bool(usage.get("recall", True)) else 0,
                retrieval_waste=0 if used else 1,
                used_in_answer=bool(used and sections),
                extra={
                    "channel": "response_context",
                    "promotion_events": promotion_events,
                    "demotion_events": demotion_events,
                },
            )

        injected_chars = 0
        if injected:
            injected_chars = len(injected)
        elif sections:
            injected_chars = len(sections[0])

        payload = {
            "context": "\n\n".join([s for s in sections if s]).strip(),
            "should_inject": bool(sections),
            "ranked": ranked,
            "topic_hits": topic_hits,
            "injection_reason": injection_reason if sections else "no_injection",
            "synthetic_candidate_ids": list(synthetic_bundle.get("candidate_ids", [])),
            "shared_context": response_projection,
            "injection_context": injection_projection,
            "arbitration_context": arbitration_projection,
            "context_assist_signals": context_assist_signals,
            "blocked_injection_count": blocked_injection,
            "decision_trace": {
                "query": message,
                "candidate_total": len(candidates),
                "ranked_total": len(ranked_all),
                "allowed_total": len(ranked),
                "blocked_total": blocked_injection,
                "blocked_reasons": sorted(
                    list(
                        {
                            str(p.get("blocked_reason", ""))
                            for p in candidate_profiles
                            if str(p.get("blocked_reason", ""))
                        }
                    )
                ),
                "decision_reasons": decision_reasons,
                "injection_reason": injection_reason if sections else "no_injection",
            },
        }
        final_injected_count = len(usage_items)
        high_used = 0
        low_used = 0
        for item in usage_items:
            tid = str(item.get("id", ""))
            trust = str(profile_by_id.get(tid, {}).get("trust", item.get("trust_level", "hypothesis")))
            if trust in {"hard_trust", "soft_trust"}:
                high_used += 1
            else:
                low_used += 1
        used_chars = injected_chars
        snapshot = {
            "query_type": str(budget.get("query_type", "direct")),
            "complexity_level": str(budget.get("complexity_level", "simple")),
            "candidate_count": int(len(candidate_profiles)),
            "injectable_count": int(batch_profile.get("injectable_count", 0)),
            "final_injected_count": int(final_injected_count),
            "budget_top_k": int(top_k),
            "budget_chars": int(budget_chars),
            "used_chars": int(used_chars),
            "high_trust_ratio": round(high_used / max(1, final_injected_count), 4),
            "hypothesis_ratio": round(low_used / max(1, final_injected_count), 4),
            "blocked_count": int(batch_profile.get("blocked_count", 0)),
            "fallback_used": bool("fallback" in injection_reason),
            "topic_state": str(budget.get("topic_state", "continue")),
            "source_diversity": int(batch_profile.get("source_diversity", 0)),
            "avg_query_coverage": float(batch_profile.get("avg_query_coverage", 0.0)),
            "recent_topic_consistency": float(recent_topic_consistency),
        }
        payload["context_snapshot"] = snapshot
        self._append_context_snapshot(snapshot)
        # keep a short in-memory tail for next-round topic consistency.
        if not hasattr(self, "_recent_query_tokens"):
            self._recent_query_tokens = collections.deque(maxlen=24)
        self._recent_query_tokens.append(self._query_tokens(message))
        if errors:
            payload["errors"] = errors
        if not sections and not decision_reasons:
            dt = payload.get("decision_trace", {})
            if isinstance(dt, dict):
                dt["decision_reasons"] = ["no_candidates_or_budget_blocked"]
                payload["decision_trace"] = dt
        try:
            payload["expression_mode"] = self._build_expression_mode_context(message, payload)
        except (OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
            logger.warning("expression_mode_context_build_failed err=%s", exc)
            payload["expression_mode"] = RouterDecision(reason="router_fallback_normal").to_dict()
        expression_mode_raw = payload.get("expression_mode", {})
        expression_mode = expression_mode_raw if isinstance(expression_mode_raw, dict) else {}
        system_prompt_extra = str(expression_mode.get("prompt_extra", "") or "")
        payload["system_prompt_extra"] = system_prompt_extra
        context_text = str(payload.get("context", "") or "")
        if system_prompt_extra and context_text:
            payload["context_with_expression"] = (
                f"[SYSTEM_PROMPT_EXTRA]\n{system_prompt_extra}\n\n[MEMORY_CONTEXT]\n{context_text}"
            )
        elif system_prompt_extra:
            payload["context_with_expression"] = f"[SYSTEM_PROMPT_EXTRA]\n{system_prompt_extra}"
        else:
            payload["context_with_expression"] = context_text
        return payload

    def _maybe_generate_synthetic_candidates(self) -> dict[str, Any]:
        if not self.synthesizer:
            return {"status": "disabled"}

        synth_cfg = self.config["settings"]["memory"].get("synthetic_memory", {})
        if not synth_cfg.get("auto_generate_on_interaction", True):
            return {"status": "skipped", "reason": "auto_generate_disabled"}

        interval_hours = int(synth_cfg.get("auto_generate_interval_hours", 6))
        limit = int(synth_cfg.get("auto_generate_limit", 5))
        state_file = self.config["memory_dir"] / "synthetic_runtime_state.json"

        now = self._utc_now()
        state = {"last_run": "", "last_count": 0}
        if state_file.exists():
            try:
                state = json.loads(state_file.read_text(encoding="utf-8"))
            except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
                logger.debug("synthetic_runtime_state_load_failed file=%s error=%s", state_file, exc)
                state = {"last_run": "", "last_count": 0}

        last_run = state.get("last_run", "")
        due = True
        if last_run:
            try:
                raw = str(last_run).strip()
                if raw.endswith("Z"):
                    raw = raw[:-1] + "+00:00"
                last_dt = datetime.fromisoformat(raw)
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                else:
                    last_dt = last_dt.astimezone(timezone.utc)
                due = (now - last_dt).total_seconds() >= max(1, interval_hours) * 3600
            except (TypeError, ValueError) as exc:
                logger.debug("synthetic_runtime_state_last_run_parse_failed raw=%s error=%s", last_run, exc)
                due = True

        if not due:
            return {"status": "skipped", "reason": "interval_not_due"}

        try:
            candidates = self.synthesizer.generate_candidates(limit=limit)
            atomic_write_json(
                state_file,
                {"last_run": now.isoformat(), "last_count": len(candidates)},
                ensure_ascii=False,
                indent=2,
            )
            return {"status": "success", "generated": len(candidates)}
        except (OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
            logger.warning("synthetic_auto_generate_failed limit=%s error=%s", limit, exc)
            return {"status": "error", "error": str(exc)}

    def _get_synthetic_context_bundle(self, limit: int = 2) -> dict[str, Any]:
        if not getattr(self, "synthesizer", None):
            return {"text": "", "candidate_ids": []}
        synthesizer = self.synthesizer
        if synthesizer is None:
            return {"text": "", "candidate_ids": []}
        try:
            items = synthesizer.list_reasoning_candidates(limit=limit)
        except (OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
            logger.debug("synthetic_context_candidates_load_failed limit=%s error=%s", limit, exc)
            return {"text": "", "candidate_ids": []}
        if not items:
            return {"text": "", "candidate_ids": []}
        lines = ["## Synthesized Insights"]
        candidate_ids: list[str] = []
        for idx, item in enumerate(items, start=1):
            statement = str(item.get("statement", "")).replace("\n", " ")[:180]
            cid = str(item.get("candidate_id", ""))
            if cid:
                candidate_ids.append(cid)
            lines.append(f"{idx}. {statement}")
        return {"text": "\n".join(lines), "candidate_ids": candidate_ids}

    def _get_synthetic_context(self, limit: int = 2) -> str:
        return str(self._get_synthetic_context_bundle(limit=limit).get("text", ""))

    def add_to_short_term(self, item: str, source: str = "interaction") -> None:
        """Add item to short-term memory and persist it for cross-session recovery."""
        self.short_term_memory.append(item)

        short_cfg = self.config["settings"]["memory"].get("short_term", {})
        if short_cfg.get("persist_enabled", True):
            try:
                self.working_memory.append_item(
                    content=item,
                    importance=self._estimate_importance(item),
                    topic=self._infer_topic(item),
                    source=source,
                )
            except (OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
                logger.warning("Working memory persist warning: %s", exc)

        # When short-term memory is full, persist oldest item to structured storage
        if len(self.short_term_memory) == self.short_term_memory.maxlen:
            oldest_item = self.short_term_memory[0]
            self._extract_and_store_entities(oldest_item)

    def _extract_and_store_entities(self, text: str) -> None:
        """Extract entities from text and store in SQLite (simple rule-based)."""
        # Simple entity extraction: capitalized words (potential proper nouns)
        # This is a basic implementation - can be enhanced later
        entities = re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b", text)

        # Store individual entities
        for entity in entities:
            self.sqlite_store.add_entity(entity, "proper_noun")

        # Store simple relations (adjacent entities)
        for i in range(len(entities) - 1):
            self.sqlite_store.add_relation(entities[i], "related_to", entities[i + 1])

    def get_recent(self, n: int = 10) -> list[str]:
        """Get recent items from short-term memory."""
        return list(list(self.short_term_memory)[-n:])

    def restore_short_term_by_topic(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Restore persisted short-term memories related to a topic/query."""
        return self.working_memory.restore_by_topic(query, limit=limit)

    def save(self, key: str, content: str) -> None:
        """Save content to long-term memory (currently file-based)."""
        if getattr(self, "shadow", None):
            try:
                self.shadow.record_data(
                    action="write",
                    source="core.save",
                    content=str(content or ""),
                    ok=True,
                    metadata={"key": str(key or "")},
                )
            except (OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
                logger.debug("shadow_record_data_failed in core.save pre-write: %s", exc)

        safe = self._safe_text_for_memory_md(content)
        if not safe["allowed"]:
            if getattr(self, "shadow", None):
                try:
                    self.shadow.record_data(
                        action="write",
                        source="core.save",
                        content=str(content or ""),
                        ok=False,
                        error="admission_blocked",
                        metadata={"key": str(key or ""), "route": safe.get("route", "rejected")},
                    )
                except (OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
                    logger.debug("shadow_record_data_failed in core.save admission_block: %s", exc)
            return
        safe_content = str(safe["text"])
        if getattr(self, "shadow", None) and self.shadow.should_takeover_write("high"):
            try:
                self.shadow.spool_write(safe_content, source="core.save.sealed")
            except (OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
                logger.debug("shadow_spool_write_failed in core.save takeover: %s", exc)
            return
        try:
            write_out = self.write_gateway(
                f"MEMORY.md::{key}\n{safe_content}",
                source="memory_md_write",
                category="general",
                write_daily_log=False,
            )
            if str(write_out.get("status", "")) != "accepted":
                return
            assessment = self.governance.assess_memory_write(
                safe_content,
                self.get_memory_blocks(),
                {"MEMORY.md": self.file_store.read_memory_md()},
            )
            if assessment["is_duplicate"]:
                return
            # For now, just append to MEMORY.md
            current_content = self.file_store.read_memory_md()
            new_content = f"{current_content}\n\n## {key}\n{safe_content}"
            self.file_store.write_memory_md(new_content)

            # Also extract entities from the saved content
            self._extract_and_store_entities(safe_content)

            # Reindex to include new content
            self.whoosh_search.reindex_all()
            self._dispatch_knowledge_graph_ingest("MEMORY.md", key, safe_content, use_llm=True)

            # Interval-gated maintenance (backup/sync/cleanup)
            self.maintenance.run_maintenance(force=False)
            if getattr(self, "shadow", None):
                try:
                    self.shadow.handle_write_success()
                except (OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
                    logger.debug("shadow_handle_write_success_failed in core.save: %s", exc)
            self._mark_write_success("core.save")
        except (OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
            self._mark_write_failure(str(exc), "core.save")
            if getattr(self, "shadow", None):
                try:
                    self.shadow.record_data(
                        action="write",
                        source="core.save",
                        content=safe_content,
                        ok=False,
                        error=str(exc),
                        metadata={"key": str(key or ""), "phase": "main_write"},
                    )
                    self.shadow.handle_write_error(reason=str(exc), source="core.save.error")
                    self.shadow.spool_write(safe_content, source="core.save.error")
                except (OSError, RuntimeError, TypeError, ValueError, AttributeError) as shadow_exc:
                    logger.debug("shadow_error_path_failed in core.save: %s", shadow_exc)
            return
        try:
            self._run_maintenance_policy(force=False)
        except (OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
            logger.warning("Maintenance policy warning: %s", exc)

        # Interval-gated synthetic candidate generation
        synth_status = self._maybe_generate_synthetic_candidates()
        if synth_status.get("status") == "error":
            logger.warning("Synthetic generation warning: %s", synth_status.get("error"))

        # Auto-commit to Git if enabled
        if self.config["settings"]["memory"]["git"]["auto_commit"]:
            self.git_manager.commit_if_needed()

        # Refresh health snapshot/report after each interaction.
        try:
            self.monitoring.status()
        except (OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
            logger.warning("Monitoring runtime warning: %s", exc)

    def load(self, key: str) -> str | None:
        """Load content by key (placeholder implementation)."""
        # This will be enhanced with proper search in Phase 3
        content = self.file_store.read_memory_md()
        if key in content:
            # Simple key-based lookup
            lines = content.split("\n")
            for i, line in enumerate(lines):
                if line.strip() == f"## {key}":
                    # Return content until next header or end
                    result = []
                    for j in range(i + 1, len(lines)):
                        if lines[j].startswith("## "):
                            break
                        result.append(lines[j])
                    return "\n".join(result).strip()
        return None

    def write_gateway(
        self,
        text: str,
        *,
        source: str,
        category: str = "general",
        write_daily_log: bool = False,
    ) -> dict[str, Any]:
        def _audit(status: str, reason: str, record_id: str = "") -> None:
            try:
                p = Path(self.config["memory_dir"]) / "logs" / "write_gateway_audit.jsonl"
                p.parent.mkdir(parents=True, exist_ok=True)
                payload = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "status": status,
                    "reason": reason,
                    "source": str(source or "unknown"),
                    "category": str(category or "general"),
                    "record_id": str(record_id or ""),
                    "write_daily_log": bool(write_daily_log),
                }
                with p.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(payload, ensure_ascii=False) + "\n")
            except (OSError, TypeError, ValueError) as exc:
                logger.debug("write_gateway_audit_failed: %s", exc)

        payload = str(text or "").strip()
        if not payload:
            _audit("rejected", "empty_text")
            return {"status": "rejected", "reason": "empty_text"}
        admission = self.memory_admission.admit(payload, category=category)
        if not bool(admission.get("allowed", False)):
            reason = str(admission.get("reason", "admission_rejected"))
            _audit("rejected", reason)
            return {"status": "rejected", "reason": reason}
        row = append_memory_record(
            memory_dir=Path(self.config["memory_dir"]),
            text=payload,
            source=str(source or "unknown"),
            status="accepted",
        )
        if write_daily_log:
            self.file_store.append_to_daily_log(payload)
        _audit("accepted", "ok", record_id=str(row.get("id", "")))
        return {"status": "accepted", "record_id": str(row.get("id", ""))}

    # Backward-compatible alias during transition.
    def _ingest_via_gateway(
        self,
        text: str,
        *,
        source: str,
        category: str = "general",
        write_daily_log: bool = False,
    ) -> dict[str, Any]:
        return self.write_gateway(
            text,
            source=source,
            category=category,
            write_daily_log=write_daily_log,
        )

    def append_interaction(self, content: str) -> None:
        """Append interaction to daily log and short-term memory."""
        if getattr(self, "shadow", None):
            try:
                self.shadow.record_data(
                    action="write",
                    source="core.append_interaction",
                    content=str(content or ""),
                    ok=True,
                    metadata={"target": "daily_log"},
                )
            except (OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
                logger.debug("shadow_record_data_failed in append_interaction: %s", exc)
        self.governance.assess_memory_write(
            content,
            self.get_memory_blocks(),
            {"MEMORY.md": self.file_store.read_memory_md()},
        )
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        today_source = f"daily_log:{datetime.now().date().isoformat()}.md"
        self.add_to_short_term(content, source=today_source)
        if getattr(self, "shadow", None) and self.shadow.should_takeover_write("low"):
            try:
                self.shadow.spool_write(str(content or ""), source="core.append_interaction.sealed")
            except (OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
                logger.debug("shadow_spool_write_failed in append_interaction takeover: %s", exc)
            return
        try:
            ingest = self.write_gateway(
                content,
                source=today_source,
                category="general",
                write_daily_log=True,
            )
            if str(ingest.get("status", "")) != "accepted":
                return
            # Reindex to include new daily log entry + canonical record
            self.whoosh_search.reindex_all()
            if getattr(self, "shadow", None):
                try:
                    self.shadow.handle_write_success()
                except (OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
                    logger.debug("shadow_handle_write_success_failed in append_interaction: %s", exc)
            self._mark_write_success("core.append_interaction")
        except (OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
            self._mark_write_failure(str(exc), "core.append_interaction")
            if getattr(self, "shadow", None):
                try:
                    self.shadow.record_data(
                        action="write",
                        source="core.append_interaction",
                        content=str(content or ""),
                        ok=False,
                        error=str(exc),
                        metadata={"target": "daily_log"},
                    )
                    self.shadow.handle_write_error(reason=str(exc), source="core.append_interaction.error")
                    self.shadow.spool_write(str(content or ""), source="core.append_interaction.error")
                except (OSError, RuntimeError, TypeError, ValueError, AttributeError) as shadow_exc:
                    logger.debug("shadow_error_path_failed in append_interaction: %s", shadow_exc)
            return
        self._dispatch_knowledge_graph_ingest(today_source, f"Interaction {timestamp}", content, use_llm=False)

        # Interval-gated high-level context and behavior pattern analysis.
        try:
            self._run_advanced_insight(content)
        except (OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
            logger.warning("Advanced insight warning: %s", exc)

        # Interval-gated maintenance (backup/sync/cleanup)
        try:
            self.maintenance.run_maintenance(force=False)
        except (OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
            logger.warning("Maintenance runtime warning: %s", exc)
        try:
            self._run_maintenance_policy(force=False)
        except (OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
            logger.warning("Maintenance policy warning: %s", exc)

        # Interval-gated synthetic candidate generation
        synth_status = self._maybe_generate_synthetic_candidates()
        if synth_status.get("status") == "error":
            logger.warning("Synthetic generation warning: %s", synth_status.get("error"))

        # Auto-commit to Git if enabled
        if self.config["settings"]["memory"]["git"]["auto_commit"]:
            self.git_manager.commit_if_needed()

        # Refresh health snapshot/report after each interaction.
        try:
            self.monitoring.status()
        except (OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
            logger.warning("Monitoring runtime warning: %s", exc)

    def get_entity_relations(self, entity_name: str) -> list[tuple]:
        """Get relations for an entity from structured memory."""
        return self.sqlite_store.get_entity_relations(entity_name)

    def cleanup_old_memory(self) -> int:
        """Clean up old entities based on retention policy."""
        retention_days = self.config["settings"]["memory"]["learning"]["retention_days"]
        return self.sqlite_store.cleanup_old_entities(retention_days)

    def retrieve_memories(
        self,
        query: str,
        top_k: int = 5,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        source_filter: str | None = None,
        allow_semantic: bool = True,
        allow_graph: bool = True,
    ) -> list[dict[str, Any]]:
        """Unified retrieval outlet with multi-source fusion and standardized output."""
        fusion_cfg = self.config["settings"]["memory"].get("retrieval_fusion", {})
        position_decay = float(fusion_cfg.get("position_decay_factor", 0.01))
        graph_multiplier = float(fusion_cfg.get("graph_score_multiplier", 0.55))
        incremental_multiplier = float(fusion_cfg.get("incremental_score_multiplier", 0.9))
        rerank_fusion_weight = float(fusion_cfg.get("rerank_fusion_weight", 0.82))
        rerank_trust_weight = float(fusion_cfg.get("rerank_trust_weight", 0.18))
        shadow = getattr(self, "shadow", None)

        def _shadow_fallback(reason: str) -> list[dict[str, Any]]:
            if not shadow:
                return []
            try:
                rows = shadow.search_shadow(query, limit=top_k)
                shadow.record_data(
                    action="read",
                    source="core.retrieve_memories.shadow_fallback",
                    content=str(query or ""),
                    ok=bool(rows),
                    metadata={"reason": reason, "result_count": len(rows)},
                )
                return rows
            except (OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
                logger.debug("shadow_fallback_failed reason=%s error=%s", reason, exc)
                return []

        try:
            lexical = self.whoosh_search.search(query, top_k, date_from, date_to, source_filter)
        except (OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
            if shadow:
                try:
                    shadow.record_data(
                        action="read",
                        source="core.retrieve_memories",
                        content=str(query or ""),
                        ok=False,
                        error=str(exc),
                        metadata={"phase": "lexical_search"},
                    )
                except (OSError, RuntimeError, TypeError, ValueError, AttributeError) as shadow_exc:
                    logger.debug("Shadow lexical error logging skipped: %s", shadow_exc)
            return _shadow_fallback("lexical_error")

        try:
            semantic = self.semantic_search.search(query, top_k=top_k) if allow_semantic else []
        except (OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
            logger.debug("semantic_search_failed query=%s error=%s", query, exc)
            semantic = []
        incremental: list[dict[str, Any]] = []
        pipeline = getattr(self.auto_memory, "pipeline", None) if getattr(self, "auto_memory", None) else None
        indexer = getattr(pipeline, "indexer", None) if pipeline else None
        if indexer is not None and hasattr(indexer, "search"):
            try:
                inc_rows = indexer.search(query, limit=max(top_k * 3, 12))
                for row in inc_rows:
                    if not isinstance(row, dict):
                        continue
                    content = str(row.get("normalized_text", row.get("text", ""))).strip()
                    if not content:
                        continue
                    incremental.append(
                        {
                            "source": str(row.get("source", "")),
                            "title": str(row.get("category", "")) or "incremental",
                            "content": content,
                            "date": row.get("created_at", row.get("time_info")),
                            "score": float(row.get("confidence", 0.0) or 0.0),
                        }
                    )
            except (OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
                logger.debug("incremental_search_failed query=%s error=%s", query, exc)
                incremental = []

        fused: dict[str, dict[str, Any]] = {}

        def _merge(item: dict[str, Any], source_kind: str, score: float, index: int) -> None:
            source = str(item.get("source", ""))
            title = str(item.get("title", ""))
            content = str(item.get("content", ""))
            key = self._retrieval_fingerprint(source, title, content)
            base = fused.get(key)
            if not base:
                base = {
                    "id": self._build_memory_ref(source, title, content),
                    "source": source,
                    "title": title,
                    "content": content,
                    "date": item.get("date"),
                    "scores": {
                        "lexical": 0.0,
                        "semantic": 0.0,
                        "incremental": 0.0,
                        "graph": 0.0,
                        "fusion": 0.0,
                        "trust": 0.0,
                        "source_prior": 0.0,
                        "rerank": 0.0,
                    },
                    "signals": {
                        "search_type": source_kind,
                        "matched_entities": [],
                        "recommended_by": [],
                        "fused_sources": [source_kind],
                    },
                    "raw": dict(item),
                }
                fused[key] = base

            if source_kind not in base["signals"].get("fused_sources", []):
                base["signals"]["fused_sources"].append(source_kind)
            base["scores"][source_kind] = max(float(base["scores"].get(source_kind, 0.0)), float(score))
            base["scores"]["fusion"] += float(score) + max(0.0, (top_k - index) * position_decay)
            if source_kind != "lexical":
                base["signals"]["search_type"] = "hybrid"

        for idx, item in enumerate(lexical):
            _merge(item, "lexical", float(item.get("score", 0.0) or 0.0), idx)

        for idx, item in enumerate(semantic):
            _merge(item, "semantic", float(item.get("score", 0.0) or 0.0), idx)

        for idx, item in enumerate(incremental):
            score = max(0.05, float(item.get("score", 0.0) or 0.0) * incremental_multiplier)
            _merge(item, "incremental", score, idx)

        if allow_graph and self._graph_enabled():
            kg = self.knowledge_graph
            if kg is None:
                graph_results = []
            else:
                try:
                    graph_results = kg.search_related_memories(query, limit=top_k)
                except (OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
                    logger.warning("Knowledge graph search error: %s", exc)
                    graph_results = []
            for idx, item in enumerate(graph_results):
                _merge(item, "graph", float(item.get("score", 0.0) or 0.0) * graph_multiplier, idx)
                key = self._retrieval_fingerprint(
                    str(item.get("source", "")),
                    str(item.get("title", "")),
                    str(item.get("content", "")),
                )
                if key in fused:
                    fused[key]["signals"]["matched_entities"] = item.get("matched_entities", [])
                    fused[key]["signals"]["recommended_by"] = item.get("recommended_by", [])
                    fused[key]["signals"]["search_type"] = "hybrid_graph"

        ranked: list[dict[str, Any]] = []
        for row in fused.values():
            annotated = self.governance.annotate_search_result(
                {
                    "source": row.get("source", ""),
                    "title": row.get("title", ""),
                    "content": row.get("content", ""),
                    "date": row.get("date"),
                    "score": row.get("scores", {}).get("fusion", 0.0),
                }
            )
            trust = float(annotated.get("governance", {}).get("trust_score", 0.0) or 0.0)
            row["scores"]["trust"] = trust
            base_rerank = row["scores"].get("fusion", 0.0) * rerank_fusion_weight + trust * rerank_trust_weight
            prior_adj, prior_reason = self._source_prior_adjust(row, query)
            row["scores"]["source_prior"] = prior_adj
            row["scores"]["rerank"] = base_rerank + prior_adj
            if prior_reason:
                row.setdefault("signals", {})["source_prior_reason"] = prior_reason
            row["raw"]["governance"] = annotated.get("governance", {})
            ctl = self.knowledge_arbitrator.arbitrate_retrieval(row)
            row["knowledge_tier"] = ctl.get("knowledge_tier", "observation")
            row["trust_level"] = ctl.get("trust_level", "hypothesis")
            row["usage_permission"] = ctl.get("usage_permission", {"recall": True, "inject": "weak", "speak": "hint"})
            ranked.append(row)

        ranked.sort(key=lambda r: float(r.get("scores", {}).get("rerank", 0.0)), reverse=True)
        # Optional lightweight trust-tier balancing for feedback diversity.
        kc_cfg = self.config["settings"]["memory"].get("knowledge_control", {})
        mix_cfg = kc_cfg.get("retrieval_mix_balancer", {})
        if bool(mix_cfg.get("enabled", True)) and ranked:
            n = len(ranked)
            hard_ratio = float(mix_cfg.get("hard_top_ratio", 0.2))
            hypo_ratio = float(mix_cfg.get("hypothesis_bottom_ratio", 0.2))
            min_hard = int(mix_cfg.get("min_hard_count", 1))
            min_hypo = int(mix_cfg.get("min_hypothesis_count", 1))
            hard_slots = min(n, max(min_hard, int(round(n * max(0.0, hard_ratio)))))
            hypo_slots = min(n, max(min_hypo, int(round(n * max(0.0, hypo_ratio)))))
            hypo_max_trust = float(mix_cfg.get("hypothesis_max_trust_score", 0.62))

            usage_by_trust = kc_cfg.get("usage_permission_by_trust", {})

            def _perm(trust: str) -> dict[str, Any]:
                p = usage_by_trust.get(
                    trust,
                    usage_by_trust.get("isolated", {"recall": False, "inject": "none", "speak": "deny"}),
                )
                return {
                    "recall": bool(p.get("recall", False)),
                    "inject": str(p.get("inject", "none")),
                    "speak": str(p.get("speak", "deny")),
                }

            for idx, row in enumerate(ranked):
                trust_score = float(row.get("scores", {}).get("trust", 0.0) or 0.0)
                current = str(row.get("trust_level", "hypothesis"))
                if idx < hard_slots and current == "soft_trust":
                    row["trust_level"] = "hard_trust"
                    row["knowledge_tier"] = "core"
                    row["usage_permission"] = _perm("hard_trust")
                    continue
                if (
                    idx >= max(0, n - hypo_slots)
                    and current in {"soft_trust", "hard_trust"}
                    and trust_score <= hypo_max_trust
                ):
                    row["trust_level"] = "hypothesis"
                    row["knowledge_tier"] = "observation"
                    row["usage_permission"] = _perm("hypothesis")

        recalled = [r for r in ranked if bool((r.get("usage_permission") or {}).get("recall", True))]
        # Source diversity guard to avoid one source monopolizing TopK.
        if recalled:
            domain = self._query_domain(query)
            max_per_source = int(fusion_cfg.get("max_per_source", 3) or 3)
            if domain == "governance":
                max_per_source = int(fusion_cfg.get("max_per_source_governance", max_per_source) or max_per_source)
            if max_per_source > 0:
                selected: list[dict[str, Any]] = []
                overflow: list[dict[str, Any]] = []
                src_count: dict[str, int] = {}
                for row in recalled:
                    src = str(row.get("source", ""))
                    cnt = src_count.get(src, 0)
                    if cnt < max_per_source:
                        selected.append(row)
                        src_count[src] = cnt + 1
                    else:
                        overflow.append(row)
                for row in overflow:
                    if len(selected) >= top_k:
                        break
                    selected.append(row)
                recalled = selected
        output = recalled[:top_k]
        if (not output) and shadow and shadow.is_sealed():
            output = _shadow_fallback("sealed_empty")
        if getattr(self, "shadow", None):
            try:
                self.shadow.record_data(
                    action="read",
                    source="core.retrieve_memories",
                    content=str(query or ""),
                    ok=True,
                    metadata={"top_k": int(top_k), "result_count": len(output)},
                )
            except (OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
                logger.debug("Shadow read success logging skipped: %s", exc)
        return output

    def search(
        self,
        query: str,
        top_k: int = 5,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        source_filter: str | None = None,
        hybrid: bool = True,
    ) -> list[dict]:
        """Legacy search output retained; internally delegates to unified retrieval outlet."""
        rows = self.retrieve_memories(
            query,
            top_k=top_k,
            date_from=date_from,
            date_to=date_to,
            source_filter=source_filter,
            allow_semantic=hybrid,
            allow_graph=hybrid,
        )
        legacy: list[dict[str, Any]] = []
        for row in rows:
            legacy.append(
                {
                    "content": row.get("content", ""),
                    "source": row.get("source", ""),
                    "date": row.get("date", ""),
                    "title": row.get("title", ""),
                    "score": row.get("scores", {}).get("fusion", 0.0),
                    "lexical_score": row.get("scores", {}).get("lexical", 0.0),
                    "semantic_score": row.get("scores", {}).get("semantic", 0.0),
                    "graph_score": row.get("scores", {}).get("graph", 0.0),
                    "fusion_score": row.get("scores", {}).get("fusion", 0.0),
                    "rerank_score": row.get("scores", {}).get("rerank", 0.0),
                    "search_type": row.get("signals", {}).get("search_type", "lexical"),
                    "matched_entities": row.get("signals", {}).get("matched_entities", []),
                    "recommended_by": row.get("signals", {}).get("recommended_by", []),
                    "governance": row.get("raw", {}).get("governance", {}),
                    "knowledge_tier": row.get("knowledge_tier", "observation"),
                    "trust_level": row.get("trust_level", "hypothesis"),
                    "usage_permission": row.get(
                        "usage_permission", {"recall": True, "inject": "weak", "speak": "hint"}
                    ),
                }
            )
        return legacy

    def reindex_memory(self) -> None:
        """Force reindex all memory files."""
        self.whoosh_search.reindex_all()

    def git_commit(self, message: str | None = None) -> bool:
        """Manually commit memory changes to Git."""
        return self.git_manager.commit_if_needed(message)

    def git_history(self, max_count: int = 10) -> list[dict]:
        """Get Git commit history for memory files."""
        return self.git_manager.get_commit_history(max_count)

    def is_git_available(self) -> bool:
        """Check if Git integration is available and enabled."""
        return self.git_manager.is_available()

    # ========== Letta-style Features ==========

    def get_memory_blocks(self) -> dict[str, str]:
        """Get all memory blocks (human/persona/archival)."""
        return {
            "human": self.memory_blocks.get_block("human") or "",
            "persona": self.memory_blocks.get_block("persona") or "",
            "archival": self.memory_blocks.get_block("archival") or "",
        }

    def spawn_subagent(self, subagent_name: str, task: str, background: bool = False) -> dict:
        """
        Spawn a subagent to execute a task.

        Args:
            subagent_name: Name of subagent (explore/memory/recall/reflection)
            task: Task description
            background: Run in background

        Returns:
            Dict with status and result
        """
        return self._run_async(self.subagents.spawn(subagent_name, task, background))

    def list_subagents(self) -> list[dict]:
        """List all available subagents."""
        return self.subagents.list_subagents()

    def list_background_subagent_tasks(self, limit: int = 20) -> list[dict]:
        """List recent background subagent tasks."""
        if hasattr(self.subagents, "list_background_tasks"):
            return self.subagents.list_background_tasks(limit)
        return []

    def get_background_subagent_task(self, task_id: str) -> dict:
        """Get a background subagent task status."""
        if hasattr(self.subagents, "get_background_task_status"):
            return self.subagents.get_background_task_status(task_id)
        return {"status": "error", "error": "Background task status not supported"}

    def retry_background_subagent_task(self, task_id: str) -> dict:
        """Retry a background subagent task."""
        if hasattr(self.subagents, "retry_background_task"):
            return self._run_async(self.subagents.retry_background_task(task_id))
        return {"status": "error", "error": "Background task retry not supported"}

    def create_subagent(
        self, name: str, description: str, instructions: str, tools: list[str] | None = None
    ) -> dict:
        """Create a custom subagent."""
        return self.subagents.create_custom_subagent(name, description, instructions, tools)

    def learn_skill(self, trajectory: list[dict], skill_name: str, instructions: str | None = None) -> dict:
        """
        Learn a new skill from conversation trajectory.

        Args:
            trajectory: List of conversation messages
            skill_name: Name for the new skill
            instructions: Optional instructions for what to extract

        Returns:
            Dict with status and skill info
        """
        return self.skills.learn_skill_from_trajectory(trajectory, skill_name, instructions)

    def list_skills(self) -> list[dict]:
        """List all available skills."""
        return self.skills.list_skills()

    def load_skill(self, skill_name: str) -> str | None:
        """Load a skill's full content."""
        return self.skills.load_skill(skill_name)

    def create_skill(
        self, name: str, description: str, instructions: str, resources: dict[str, str] | None = None
    ) -> dict:
        """Create a new skill."""
        return self.skills.create_skill(name, description, instructions, "project", resources)

    def get_context_with_blocks(self) -> str:
        """
        Get current context with memory blocks injected.

        Returns:
            Formatted string with all memory blocks for context injection
        """
        return self.memory_blocks.export_blocks()

    def get_graph_context(self, message: str, limit: int = 5) -> dict:
        """Build graph-backed context for conversation injection."""
        if not self._graph_enabled():
            return {"enabled": False, "text": "", "entities": []}
        kg = self.knowledge_graph
        if kg is None:
            return {"enabled": False, "text": "", "entities": []}
        try:
            return kg.build_context_for_message(message, limit=limit)
        except (OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
            return {"enabled": False, "text": "", "entities": [], "error": str(exc)}

    def get_augmented_context(self, message: str, include_blocks: bool = True, graph_limit: int = 5) -> str:
        """Return memory blocks + ranked memory retrieval + graph context for response reasoning."""
        sections: list[str] = []
        if include_blocks:
            sections.append(self.get_context_with_blocks())

        memory_context = self.get_response_memory_context(message)
        if memory_context.get("context"):
            sections.append(memory_context["context"])

        graph_context = self.get_graph_context(message, limit=graph_limit)
        if graph_context.get("text"):
            sections.append(graph_context["text"])

        return "\n\n".join(part for part in sections if part).strip()

    def get_governance_report(self, limit: int = 20) -> dict:
        """Return governance diagnostics + unified core metric snapshot."""
        report = self.governance.report(limit)
        try:
            mon = self.monitoring.status()
            report["core_metrics"] = mon.get("core_metrics", {})
        except (OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
            report["core_metrics_error"] = str(exc)
        return report

    def trigger_memory_tiering(self) -> dict:
        """Archive older memory logs with maintenance as final executor."""
        if self.learning is None:
            return {"status": "disabled", "reason": "learning_unavailable"}
        plan = self.learning.build_memory_tiering_plan()
        result = self.maintenance.apply_tiering_plan(plan, owner="maintenance")
        moved = result.get("moved", [])
        if moved:
            self.reindex_memory()
            self._dispatch_graph_batch_extract(force=True)
        return {
            "status": "success",
            "plan_count": len(plan),
            "moved_files": moved,
            "execution_owner": "maintenance",
            "maintenance_result": result,
        }

    def preview_weekly_compression(self) -> dict:
        """Preview weekly compression plan without applying changes."""
        if self.learning is None:
            return {"status": "disabled", "reason": "learning_unavailable"}
        return self.learning.trigger_weekly_compression(preview_only=True)

    def trigger_reflection(self) -> dict:
        """
        Trigger sleep-time reflection (consolidate recent memories).

        Returns:
            Dict with status and reflection results
        """
        # Spawn reflection subagent
        result = self._run_async(
            self.subagents.spawn(
                "reflection",
                "Consolidate and organize recent memories",
            )
        )

        if result["status"] == "success":
            # Also trigger learning task
            try:
                if self.learning is not None:
                    self.learning.trigger_daily_learning()
                result["learning_triggered"] = True
            except (RuntimeError, OSError, TypeError, ValueError) as e:
                result["learning_triggered"] = False
                result["learning_error"] = str(e)
        if self._graph_enabled():
            maintenance = self.run_knowledge_graph_maintenance()
            result["knowledge_graph_maintenance"] = maintenance

        return result

    def search_graph_entities(self, query: str, entity_type: str | None = None, limit: int = 10) -> list[dict]:
        if not self._graph_enabled():
            return []
        kg = self.knowledge_graph
        if kg is None:
            return []
        return kg.search_entities(query, entity_type=entity_type, limit=limit)

    def list_graph_relations(
        self,
        entity_name: str | None = None,
        relation_type: str | None = None,
        direction: str = "both",
        limit: int = 10,
    ) -> list[dict]:
        if not self._graph_enabled():
            return []
        kg = self.knowledge_graph
        if kg is None:
            return []
        return kg.list_relations(
            entity_name, relation_type=relation_type, direction=direction, limit=limit
        )

    def get_graph_neighbors(
        self, entity_name: str, depth: int = 2, relation_type: str | None = None, limit: int = 10
    ) -> list[dict]:
        if not self._graph_enabled():
            return []
        kg = self.knowledge_graph
        if kg is None:
            return []
        return kg.get_neighbors(entity_name, depth=depth, relation_type=relation_type, limit=limit)

    def get_graph_related_entities(self, entity_name: str, limit: int = 10) -> list[dict]:
        if not self._graph_enabled():
            return []
        kg = self.knowledge_graph
        if kg is None:
            return []
        return kg.related_entities(entity_name, limit=limit)

    def find_graph_path(self, start_name: str, end_name: str, max_depth: int = 3) -> dict:
        if not self._graph_enabled():
            return {"status": "disabled", "path": []}
        kg = self.knowledge_graph
        if kg is None:
            return {"status": "disabled", "path": []}
        return kg.shortest_path(start_name, end_name, max_depth=max_depth)

    def batch_extract_knowledge_graph(self, limit: int | None = None, force: bool = False) -> dict:
        if not self._graph_enabled():
            return {"status": "disabled"}
        kg = self.knowledge_graph
        if kg is None:
            return {"status": "disabled"}
        return kg.batch_extract_pending_memories(limit=limit, force=force)

    def get_knowledge_graph_stats(self) -> dict:
        if not self._graph_enabled():
            return {"status": "disabled"}
        kg = self.knowledge_graph
        if kg is None:
            return {"status": "disabled"}
        return kg.stats()

    def get_knowledge_graph_timeline(self, days: int = 7, limit: int = 10) -> dict:
        if not self._graph_enabled():
            return {"status": "disabled"}
        kg = self.knowledge_graph
        if kg is None:
            return {"status": "disabled"}
        return kg.timeline(days=days, limit=limit)

    def get_knowledge_graph_health(self) -> dict:
        if not self._graph_enabled():
            return {"status": "disabled"}
        kg = self.knowledge_graph
        if kg is None:
            return {"status": "disabled"}
        return kg.health_check()

    def run_knowledge_graph_maintenance(self) -> dict:
        if not self._graph_enabled():
            return {"status": "disabled"}
        kg = self.knowledge_graph
        if kg is None:
            return {"status": "disabled"}
        decay = kg.decay_relation_weights()
        cleanup = kg.cleanup_isolated_entities()
        health = kg.health_check()
        return {
            "status": "success",
            "decay": decay,
            "cleanup": cleanup,
            "health": health,
        }

    def prepare_graph_offline_cleanup(self, limit: int = 500) -> dict[str, Any]:
        """Prepare offline cleanup report + snapshot; does not delete graph data."""
        if not self._graph_enabled():
            return {"status": "disabled"}
        kg = self.knowledge_graph
        if kg is None:
            return {"status": "disabled"}
        return kg.prepare_offline_cleanup(limit=limit)

    # ========== Advanced Skill System Features ==========

    def install_skill_from_github(self, github_url: str, scope: str = "project") -> dict:
        """
        Install skill from GitHub URL.

        Args:
            github_url: GitHub URL (e.g., https://github.com/anthropics/skills/tree/main/skills/frontend-design)
            scope: Installation scope (project/agent/global)

        Returns:
            Dict with installation status
        """
        return self.skill_installer.install_from_github(github_url, scope)

    def install_skill_from_registry(self, skill_id: str, scope: str = "project") -> dict:
        """
        Install skill from registry (e.g., @anthropic/frontend-design).

        Args:
            skill_id: Skill identifier with @ prefix
            scope: Installation scope

        Returns:
            Dict with installation status
        """
        return self.skill_installer.install_from_registry(skill_id, scope)

    def install_skill_from_file(self, file_path: str, scope: str = "project") -> dict:
        """
        Install skill from local file or directory.

        Args:
            file_path: Path to skill directory
            scope: Installation scope

        Returns:
            Dict with installation status
        """
        return self.skill_installer.install_from_file(file_path, scope)

    def uninstall_skill(self, skill_name: str, scope: str = "project") -> dict:
        """
        Uninstall a skill.

        Args:
            skill_name: Name of skill
            scope: Scope to uninstall from

        Returns:
            Dict with uninstallation status
        """
        return self.skill_installer.uninstall(skill_name, scope)

    def list_installed_skills(self) -> list[dict]:
        """List all installed skills."""
        return self.skill_installer.list_installed()

    def get_built_in_skills(self) -> list[dict]:
        """Get list of available built-in skills."""
        return self.built_in_skills.get_built_in_skills()

    def install_built_in_skill(self, skill_name: str) -> dict:
        """
        Install a built-in skill.

        Args:
            skill_name: Name of built-in skill

        Returns:
            Dict with installation status
        """
        return self.built_in_skills.install_built_in(skill_name)

    def install_all_built_in_skills(self) -> dict:
        """Install all built-in skills."""
        return self.built_in_skills.install_all_built_in()

    def get_relevant_skills(self, context: str, top_k: int = 3) -> list[dict]:
        """
        Get skills relevant to current context.

        Args:
            context: Current conversation or task context
            top_k: Number of skills to return

        Returns:
            List of relevant skills with scores
        """
        return self.skill_discovery.get_relevant_skills(context, top_k)

    def get_system_prompt_with_skills(self) -> str:
        """
        Get system prompt injection with available skills.

        Returns:
            Formatted string for system prompt
        """
        return self.skill_discovery.get_system_prompt_injection()

    def load_skill_with_tool(self, skill_name: str) -> dict:
        """
        Load skill using tool call (keeps context clean).

        Args:
            skill_name: Name of skill to load

        Returns:
            Dict with skill content
        """
        skill_content = self.skills.load_skill(skill_name)
        if skill_content:
            return {"status": "success", "skill_name": skill_name, "content": skill_content}
        else:
            return {"status": "error", "error": f'Skill "{skill_name}" not found or failed to load'}

    def add_skill_registry(self, name: str, url: str, type: str = "github") -> dict:
        """
        Add a skill registry source.

        Args:
            name: Registry name
            url: Registry URL
            type: Registry type (github/direct)

        Returns:
            Dict with registry info
        """
        registry = self.skill_registry.add_registry(name, url, type)
        return {"status": "success", "message": f'Added registry "{name}"', "registry": registry}

    def check_skill_updates(self) -> list[dict]:
        """Check for skill updates."""
        return self.skill_installer.check_updates()

    def update_skill(self, skill_name: str) -> dict:
        """
        Update a skill to latest version.

        Args:
            skill_name: Name of skill to update

        Returns:
            Dict with update status
        """
        return self.skill_installer.update_skill(skill_name)

    # ========== GitHub Skill Marketplace Features ==========

    def _skills_online_features_enabled(self) -> bool:
        """Feature gate for online skill ecosystem (discovery/marketplace/auto-install)."""
        skills_cfg = self.config["settings"]["memory"].get("skills_system", {})
        github_enabled = bool(skills_cfg.get("github_enabled", False))
        marketplace_enabled = bool(skills_cfg.get("marketplace_enabled", False))
        return github_enabled and marketplace_enabled

    def _skill_auto_install_enabled(self) -> bool:
        skills_cfg = self.config["settings"]["memory"].get("skills_system", {})
        return bool(skills_cfg.get("auto_install_enabled", False))

    def _sync_github_skills(self) -> None:
        """Synchronize skills from GitHub."""
        if not self._skills_online_features_enabled():
            return
        try:
            # Get skills from GitHub
            github_skills = self.github_discovery.search_skills(limit=100)

            # Update search index
            if github_skills:
                self.skill_search_index.update_index(github_skills)
        except (RuntimeError, OSError, TypeError, ValueError) as e:
            logger.warning("Error syncing GitHub skills: %s", e)

    def _sync_github_skills_async(self) -> None:
        """Run GitHub skill sync asynchronously so core startup remains responsive."""
        skills_cfg = self.config["settings"]["memory"].get("skills_system", {})
        if not self._skills_online_features_enabled():
            return
        if not bool(skills_cfg.get("sync_on_boot", False)):
            return

        def runner() -> None:
            self._sync_github_skills()

        threading.Thread(target=runner, daemon=True).start()

    def search_github_skills(
        self,
        query: str | None = None,
        category: str | None = None,
        tags: list[str] | None = None,
        min_stars: int = 0,
        sort_by: str = "stars",
        limit: int = 20,
    ) -> list[dict]:
        """
        Search skills from GitHub marketplace.

        Args:
            query: Search query string
            category: Filter by category
            tags: Filter by tags
            min_stars: Minimum star count
            sort_by: Sort by (stars/updated/name)
            limit: Maximum results

        Returns:
            List of skill metadata
        """
        if not self._skills_online_features_enabled():
            return []
        return self.github_discovery.search_skills(
            query=query,
            category=category,
            tags=tags,
            min_stars=min_stars,
            sort_by=sort_by,
            limit=limit,
        )

    def search_skills_local(
        self,
        query: str,
        category: str | None = None,
        tags: list[str] | None = None,
        min_stars: int = 0,
        limit: int = 10,
    ) -> list[dict]:
        """
        Search skills from local index.

        Args:
            query: Search query
            category: Filter by category
            tags: Filter by tags
            min_stars: Minimum stars
            limit: Maximum results

        Returns:
            List of matching skills
        """
        return self.skill_search_index.search(
            query=query, category=category, tags=tags, min_stars=min_stars, limit=limit
        )

    def get_skill_categories(self) -> list[str]:
        """Get all available skill categories."""
        return self.skill_search_index.get_categories()

    def get_skill_tags(self) -> list[str]:
        """Get all available skill tags."""
        return self.skill_search_index.get_tags()

    def suggest_skills(self, prefix: str, limit: int = 5) -> list[str]:
        """
        Get skill name suggestions.

        Args:
            prefix: Prefix to match
            limit: Maximum suggestions

        Returns:
            List of skill names
        """
        return self.skill_search_index.suggest(prefix, "name", limit)

    def get_trending_skills(self, days: int = 7, limit: int = 10) -> list[dict]:
        """
        Get trending skills from GitHub.

        Args:
            days: Number of days to consider
            limit: Maximum results

        Returns:
            List of trending skills
        """
        if not self._skills_online_features_enabled():
            return []
        return self.github_discovery.get_trending_skills(days, limit)

    def get_skill_recommendations(self, context: str, limit: int = 5) -> list[dict]:
        """
        Get skill recommendations based on context.

        Args:
            context: Current context or task description
            limit: Maximum recommendations

        Returns:
            List of recommended skills
        """
        if not self._skills_online_features_enabled():
            return []
        return self.github_discovery.get_skill_recommendations(context, limit)

    def install_skill_from_github_search(self, skill_name: str, repository: str | None = None) -> dict:
        """
        Install a skill found via GitHub search.

        Args:
            skill_name: Name of skill to install
            repository: Optional repository name (owner/repo)

        Returns:
            Installation result
        """
        if not self._skills_online_features_enabled():
            return {
                "status": "disabled",
                "error": "GitHub skill discovery/marketplace is disabled by skills_system config",
            }
        if not self._skill_auto_install_enabled():
            return {
                "status": "disabled",
                "error": "Skill auto install is disabled by skills_system config",
            }

        # Search for the skill
        skills = self.search_github_skills(query=skill_name, limit=1)

        if not skills:
            return {"status": "error", "error": f'Skill "{skill_name}" not found on GitHub'}

        skill = skills[0]

        # Build GitHub URL
        if repository:
            owner, repo = repository.split("/")
        else:
            owner, repo = skill["repository"].split("/")

        github_url = f"https://github.com/{owner}/{repo}/tree/main/{skill['path']}"

        # Install
        return self.skill_installer.install_from_github(github_url)

    def get_github_skill_catalog(self, org: str = "openclaw") -> dict:
        """
        Get complete skill catalog from a GitHub organization.

        Args:
            org: GitHub organization name

        Returns:
            Complete catalog dictionary
        """
        if not self._skills_online_features_enabled():
            return {"status": "disabled", "org": org, "skills": [], "error": "GitHub skill catalog is disabled"}
        return self.github_discovery.get_skill_catalog(org)

    def get_index_stats(self) -> dict:
        """Get search index statistics."""
        return self.skill_search_index.get_index_stats()

    def refresh_skill_index(self) -> dict:
        """
        Manually refresh skill index from GitHub.

        Returns:
            Status dictionary
        """
        if not self._skills_online_features_enabled():
            return {
                "status": "disabled",
                "message": "GitHub skill index refresh is disabled by skills_system config",
                "total_indexed": 0,
            }
        try:
            # Get fresh data from GitHub
            skills = self.github_discovery.search_skills(limit=500)

            # Clear and rebuild index
            self.skill_search_index.clear_index()
            count = self.skill_search_index.update_index(skills)

            return {
                "status": "success",
                "message": f"Indexed {count} skills from GitHub",
                "total_indexed": count,
            }
        except (RuntimeError, OSError, TypeError, ValueError) as e:
            return {"status": "error", "error": str(e)}

    # ========== Letta-Style Self-Improvement Features ==========

    def remember(
        self,
        instruction: str,
        content: str | None = None,
        auto_generate_reason: bool = True,
        validate: bool = True,
        use_llm: bool = True,
    ) -> dict:
        """
        Enhanced /remember command with validation and history tracking.

        Args:
            instruction: What to remember
            content: Optional specific content
            auto_generate_reason: Whether to auto-generate reason
            validate: Whether to validate the improvement

        Returns:
            Dict with status and details
        """
        instruction_safe = self._safe_text_for_memory_md(instruction)
        if not instruction_safe["allowed"]:
            return {
                "status": "blocked",
                "message": "remember blocked by admission governance",
                "route": instruction_safe.get("route"),
                "reasons": instruction_safe.get("reasons", []),
            }
        safe_instruction = str(instruction_safe["text"])
        safe_content = None
        if content is not None:
            content_safe = self._safe_text_for_memory_md(content)
            if not content_safe["allowed"]:
                return {
                    "status": "blocked",
                    "message": "remember content blocked by admission governance",
                    "route": content_safe.get("route"),
                    "reasons": content_safe.get("reasons", []),
                }
            safe_content = str(content_safe["text"])

        result = self._run_async(
            self.self_improvement.remember(
                safe_instruction,
                safe_content,
                auto_generate_reason,
                validate,
                use_llm,
            )
        )
        if result.get("status") == "success":
            self._dispatch_knowledge_graph_ingest(
                "remember",
                safe_instruction[:80],
                safe_content or safe_instruction,
                use_llm=use_llm,
            )
        return result

    def get_improvement_history(
        self, limit: int = 10, improvement_type: str | None = None, status: str | None = None
    ) -> list[dict]:
        """
        Get self-improvement history.

        Args:
            limit: Maximum number of records
            improvement_type: Filter by type (memory_edit/skill_create/etc)
            status: Filter by validation status

        Returns:
            List of improvement records
        """
        return self.self_improvement.get_improvement_history(limit, improvement_type, status)

    def get_self_improvement_metrics(self) -> dict:
        """
        Get self-improvement metrics.

        Returns:
            Dict with metrics
        """
        return self.self_improvement.get_metrics()

    def add_validation_test(self, test_type: str, name: str, input_data: dict, expected_output: Any) -> dict:
        """
        Add a validation test case.

        Args:
            test_type: Type of test (memory/skill/performance)
            name: Test name
            input_data: Test input
            expected_output: Expected output

        Returns:
            Dict with status
        """
        return self.self_improvement.add_test_case(test_type, name, input_data, expected_output)

    def run_validation_suite(self) -> dict:
        """
        Run full validation suite on current state.

        Returns:
            Dict with validation results
        """
        return self.self_improvement.run_validation_suite()

    def rollback_improvement(self, improvement_id: str) -> dict:
        """
        Rollback a specific improvement.

        Args:
            improvement_id: ID of improvement to rollback

        Returns:
            Dict with status
        """
        # Find improvement in history
        for record in self.self_improvement.history:
            if record["id"] == improvement_id:
                # Create rollback record
                from .self_improvement import ImprovementRecord, ValidationStatus

                rollback = ImprovementRecord(
                    id=self.self_improvement._generate_improvement_id(),
                    timestamp=datetime.now(),
                    improvement_type=ImprovementType(record["improvement_type"]),
                    description=f"Rollback of {record['id']}",
                    reason="Manual rollback",
                    before_state=record["after_state"],
                    after_state=record["before_state"],
                    validation_status=ValidationStatus.VALIDATED,
                    rolled_back=False,
                )

                # Apply rollback
                self.self_improvement._rollback_improvement(rollback)

                # Record rollback
                self.self_improvement.history.append(rollback.to_dict())
                self.self_improvement._save_history()

                return {
                    "status": "success",
                    "message": f"Rolled back improvement {improvement_id}",
                    "rollback_id": rollback.id,
                }

        return {"status": "error", "error": f"Improvement {improvement_id} not found"}

    # ========== LLM Control APIs ==========

    def toggle_llm(self, enabled: bool) -> dict:
        """
        Toggle LLM features on/off.

        Args:
            enabled: True to enable, False to disable

        Returns:
            Dict with status
        """
        if hasattr(self.self_improvement, "toggle_llm"):
            return self.self_improvement.toggle_llm(enabled)
        else:
            return {"status": "error", "error": "LLM features not available"}

    def get_llm_stats(self) -> dict:
        """
        Get LLM usage statistics.

        Returns:
            Dict with stats
        """
        if hasattr(self.self_improvement, "get_llm_stats"):
            return self.self_improvement.get_llm_stats()
        else:
            return {"status": "error", "error": "LLM features not available"}

    def get_improvement_summary(self, limit: int = 10) -> dict:
        """
        Get comprehensive improvement summary.

        Args:
            limit: Number of recent records

        Returns:
            Dict with summary
        """
        if hasattr(self.self_improvement, "get_improvement_summary"):
            return self.self_improvement.get_improvement_summary(limit)
        else:
            return {"status": "error", "error": "Enhanced features not available"}

    async def detect_patterns(self, conversations: list[dict], use_llm: bool = True) -> dict:
        """
        Detect user behavior patterns.

        Args:
            conversations: Conversation history
            use_llm: Whether to use LLM

        Returns:
            Dict with detected patterns
        """
        if hasattr(self.self_improvement, "detect_user_patterns"):
            return await self.self_improvement.detect_user_patterns(conversations, use_llm)
        else:
            return {"status": "error", "error": "Pattern detection not available"}

    def get_model_info(self) -> dict:
        """
        Get local LLM model information.

        Returns:
            Dict with model info
        """
        if self.llm_enabled and hasattr(self.self_improvement, "llm"):
            return self.self_improvement.llm.get_model_info()
        else:
            return {"enabled": False, "message": "LLM not enabled"}

    def run_learning_tasks(self) -> None:
        """Run any pending active learning tasks."""
        if self.learning is not None:
            self.learning.run_pending_tasks()

    def trigger_daily_learning(self, date_str: str | None = None) -> None:
        """Manually trigger daily learning task."""
        if self.learning is None:
            return
        self.learning.trigger_daily_learning(date_str)
        if self._graph_enabled():
            self._dispatch_graph_batch_extract()

    def trigger_weekly_compression(self, confirm: bool = False) -> dict:
        """Manually trigger weekly compression task."""
        if self.learning is None:
            return {"status": "disabled", "reason": "learning_unavailable"}
        return self.learning.trigger_weekly_compression(confirm=confirm)

    def list_archived_logs(self, limit: int = 50) -> list[dict]:
        """List archived daily logs."""
        if self.learning is None:
            return []
        return self.learning.list_archived_logs(limit=limit)

    def restore_archived_logs(self, date_prefix: str | None = None, limit: int | None = None) -> dict:
        """Restore archived daily logs back into the active memory directory."""
        if self.learning is None:
            return {"status": "disabled", "restored": []}
        restored = self.learning.restore_archived_logs(date_prefix=date_prefix, limit=limit)
        if restored:
            self.reindex_memory()
        return {"status": "success", "restored": restored}

    def generate_synthetic_candidates(self, limit: int = 20) -> dict:
        """Generate synthetic memory candidates from graph patterns."""
        if not self.synthesizer:
            return {"status": "disabled", "candidates": []}
        candidates = self.synthesizer.generate_candidates(limit=limit)
        return {"status": "success", "candidates": candidates}

    def list_synthetic_candidates(self, status: str = "review", limit: int = 20) -> dict:
        """List synthetic memory candidates."""
        if not self.synthesizer:
            return {"status": "disabled", "candidates": []}
        return {
            "status": "success",
            "candidates": self.synthesizer.list_candidates(status=status, limit=limit),
        }

    def confirm_synthetic_candidates(
        self, candidate_ids: list[str] | None = None, min_score: float | None = None
    ) -> dict:
        """Confirm candidate memories and write them into memory."""
        if not self.synthesizer:
            return {"status": "disabled", "accepted": []}
        return self.synthesizer.confirm_candidates(candidate_ids=candidate_ids, min_score=min_score)

    def reject_synthetic_candidates(self, candidate_ids: list[str]) -> dict:
        """Reject candidate memories."""
        if not self.synthesizer:
            return {"status": "disabled", "rejected": []}
        return self.synthesizer.reject_candidates(candidate_ids)

    def review_synthetic_candidates(self, decisions: list[dict[str, Any]]) -> dict:
        """Batch review synthetic candidates with accept/reject decisions."""
        if not self.synthesizer:
            return {"status": "disabled", "accepted": 0, "rejected": 0}
        result = self.synthesizer.review_candidates(decisions)
        result["status"] = "success"
        return result

    def get_synthetic_health(self) -> dict[str, Any]:
        """Return synthetic candidate/graph health indicators."""
        if not self.synthesizer:
            return {"status": "disabled"}
        return {"status": "success", "health": self.synthesizer.health_report()}

    def preview_rollback_auto_approved_synthetic(self, since_hours: int = 1) -> dict[str, Any]:
        """Preview auto-approved synthetic memories that would be rolled back in a time window."""
        if not self.synthesizer:
            return {"status": "disabled"}
        return self.synthesizer.preview_rollback_auto_approved(since_hours=since_hours)

    def rollback_auto_approved_synthetic(self, since_hours: int = 1) -> dict[str, Any]:
        """Rollback auto-approved synthetic memories in a time window (status -> revoked)."""
        if not self.synthesizer:
            return {"status": "disabled"}
        return self.synthesizer.rollback_auto_approved(since_hours=since_hours)

    def rebalance_synthetic_candidates(
        self, max_auto_accept: int = 40, apply_writeback: bool = False
    ) -> dict[str, Any]:
        """Rebalance legacy review pool into accepted/rejected/review by current thresholds."""
        if not self.synthesizer:
            return {"status": "disabled"}
        raw_result = self.synthesizer.rebalance_review_queue(
            max_auto_accept=max_auto_accept,
            apply_writeback=apply_writeback,
        )
        result: dict[str, Any] = dict(raw_result)
        result["status"] = "success"
        return result

    def discover_synthetic_gaps(self, limit: int = 10) -> dict:
        """Discover knowledge gaps from the graph."""
        if not self.synthesizer:
            return {"status": "disabled", "gaps": []}
        return self.synthesizer.discover_gaps(limit=limit)

    def record_memory_feedback(
        self,
        memory_id: str,
        category: str,
        signal: str,
        helpful: bool,
        note: str = "",
        source: str = "user",
        confidence: float = 0.0,
    ) -> dict[str, Any]:
        if not self.auto_memory or not getattr(self.auto_memory, "pipeline", None):
            return {"status": "disabled", "reason": "auto_memory_pipeline_unavailable"}
        return self.auto_memory.record_feedback(
            memory_id=memory_id,
            category=category,
            signal=signal,
            helpful=helpful,
            note=note,
            source=source,
            confidence=confidence,
        )

    def weekly_threshold_suggestions(self) -> dict[str, Any]:
        if not self.auto_memory or not getattr(self.auto_memory, "pipeline", None):
            return {"status": "disabled", "reason": "auto_memory_pipeline_unavailable"}
        return self.auto_memory.weekly_threshold_suggestions()

    def get_context_optimization_suggestions(self, window: int = 300) -> dict[str, Any]:
        """Analyze context snapshots and output non-destructive tuning suggestions."""
        wm_cfg = self.config["settings"]["memory"].get("working_memory", {})
        raw = str(wm_cfg.get("context_snapshot_log_file", "memory/context_snapshots.jsonl"))
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = self.config["workspace_dir"] / path
        if not path.exists():
            return {"status": "skipped", "reason": "snapshot_not_found", "suggestions": []}

        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                logger.debug("context_budget_snapshot_row_invalid file=%s error=%s", path, exc)
                continue
        rows = rows[-max(20, int(window)) :]
        if not rows:
            return {"status": "skipped", "reason": "no_snapshot_rows", "suggestions": []}

        def _avg(items: list[float]) -> float:
            return round(sum(items) / max(1, len(items)), 4)

        simple = [r for r in rows if str(r.get("complexity_level", "")) == "simple"]
        complex_rows = [r for r in rows if str(r.get("complexity_level", "")) == "complex"]
        all_rows = rows

        def _collect(group: list[dict[str, Any]]) -> dict[str, float]:
            if not group:
                return {}
            used_ratio = []
            fallback = 0
            blocked = []
            high = []
            low = []
            consistency = []
            for r in group:
                bchars = float(r.get("budget_chars", 0) or 0)
                used = float(r.get("used_chars", 0) or 0)
                if bchars > 0:
                    used_ratio.append(min(1.5, used / bchars))
                fallback += 1 if bool(r.get("fallback_used", False)) else 0
                blocked.append(float(r.get("blocked_count", 0) or 0))
                high.append(float(r.get("high_trust_ratio", 0.0) or 0.0))
                low.append(float(r.get("hypothesis_ratio", 0.0) or 0.0))
                consistency.append(float(r.get("recent_topic_consistency", 0.0) or 0.0))
            return {
                "used_ratio_avg": _avg(used_ratio) if used_ratio else 0.0,
                "fallback_rate": round(fallback / max(1, len(group)), 4),
                "blocked_avg": _avg(blocked),
                "high_ratio_avg": _avg(high),
                "low_ratio_avg": _avg(low),
                "consistency_avg": _avg(consistency),
                "count": float(len(group)),
            }

        ms = _collect(simple)
        mc = _collect(complex_rows)
        ma = _collect(all_rows)

        suggestions: list[dict[str, Any]] = []
        if ms and ms.get("used_ratio_avg", 0.0) < 0.45:
            suggestions.append(
                {
                    "key": "dynamic_injection_budget.simple_max_chars",
                    "direction": "decrease",
                    "reason": "simple dialog budget utilization is low",
                    "current_signal": ms.get("used_ratio_avg", 0.0),
                    "suggested_factor": 0.9,
                }
            )
        if ms and ms.get("fallback_rate", 0.0) > 0.2:
            suggestions.append(
                {
                    "key": "dynamic_injection_budget.simple_top_k",
                    "direction": "increase",
                    "reason": "simple dialog fallback rate is high",
                    "current_signal": ms.get("fallback_rate", 0.0),
                    "suggested_delta": 1,
                }
            )
        if mc and mc.get("high_ratio_avg", 1.0) < 0.7:
            suggestions.append(
                {
                    "key": "dynamic_injection_budget.low_trust_ratio_cap",
                    "direction": "decrease",
                    "reason": "complex dialog high-trust injection ratio is below target",
                    "current_signal": mc.get("high_ratio_avg", 0.0),
                    "suggested_delta": -0.05,
                }
            )
        if ma and ma.get("blocked_avg", 0.0) > 2.0:
            suggestions.append(
                {
                    "key": "knowledge_control.usage_permission_by_trust",
                    "direction": "review",
                    "reason": "blocked candidates remain high; review inject permissions by trust tier",
                    "current_signal": ma.get("blocked_avg", 0.0),
                }
            )
        if ma and ma.get("consistency_avg", 0.0) < 0.15:
            suggestions.append(
                {
                    "key": "dynamic_injection_budget.topic_continue_min_consistency",
                    "direction": "decrease",
                    "reason": "cross-turn consistency stays low; continue gate may be too strict",
                    "current_signal": ma.get("consistency_avg", 0.0),
                    "suggested_delta": -0.03,
                }
            )

        report = {
            "status": "success",
            "window": int(window),
            "stats": {"all": ma, "simple": ms, "complex": mc},
            "suggestions": suggestions,
        }
        report_path = self.config["workspace_dir"] / "memory" / "context_optimization_suggestions_latest.json"
        try:
            atomic_write_json(report_path, report, ensure_ascii=False, indent=2)
            report["report_file"] = str(report_path)
        except OSError as exc:
            logger.debug("context_optimization_report_write_failed: %s", exc)
        return report

    def list_pending_reviews(self) -> dict[str, Any]:
        if not self.auto_memory or not getattr(self.auto_memory, "pipeline", None):
            return {"status": "disabled", "items": []}
        pipeline = self.auto_memory.pipeline
        if pipeline is None:
            return {"status": "disabled", "items": []}
        if not hasattr(pipeline, "review_pending"):
            return {"status": "disabled", "items": []}
        return {"status": "success", "items": pipeline.review_pending()}

    def batch_review(
        self,
        mode: str = "accept_all",
        limit: int | None = None,
        accept_conf_min: float = 0.62,
        reject_conf_max: float = 0.20,
        per_category_limit: int | None = None,
        drain_reject_conf_max: float = 0.50,
    ) -> dict[str, Any]:
        if not self.auto_memory or not getattr(self.auto_memory, "pipeline", None):
            return {"status": "disabled"}
        pipeline = self.auto_memory.pipeline
        if pipeline is None:
            return {"status": "disabled"}
        try:
            from ms8.app.review.batch_review import BatchReview

            runner = BatchReview(pipeline.review_service)
            result = runner.apply(
                mode=mode,
                limit=limit,
                accept_conf_min=accept_conf_min,
                reject_conf_max=reject_conf_max,
                per_category_limit=per_category_limit,
                drain_reject_conf_max=drain_reject_conf_max,
            )
            return {
                "status": "success",
                "reviewed": result.reviewed,
                "accepted": result.accepted,
                "rejected": result.rejected,
                "items": [i.__dict__ for i in result.items],
            }
        except (ImportError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
            logger.warning("batch_review_failed mode=%s limit=%s error=%s", mode, limit, exc)
            return {"status": "error", "error": str(exc)}

    def relabel_review_item(self, memory_id: str, category: str, notes: str = "") -> dict[str, Any]:
        if not self.auto_memory or not getattr(self.auto_memory, "pipeline", None):
            return {"status": "disabled"}
        pipeline = self.auto_memory.pipeline
        if pipeline is None:
            return {"status": "disabled"}
        try:
            from ms8.app.review.batch_review import BatchReview as RealBatchReview

            runner = RealBatchReview(pipeline.review_service)
            ok = runner.relabel(memory_id=memory_id, new_category=category, notes=notes)
            return {"status": "success" if ok else "not_found", "memory_id": memory_id}
        except (ImportError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
            logger.warning("relabel_review_item_failed memory_id=%s category=%s error=%s", memory_id, category, exc)
            return {"status": "error", "error": str(exc)}

    def run_meta_cognition(self, period: str | None = None) -> dict:
        """Run meta-cognition monitoring and return report."""
        meta_cognition = getattr(self, "meta_cognition", None)
        if not meta_cognition:
            return {"status": "disabled"}
        meta_cfg = self.config["settings"]["memory"].get("meta_cognition", {})
        period = period or meta_cfg.get("report_period", "daily")
        conversations = self._collect_recent_conversations(meta_cfg.get("max_conversations", 120))
        report = self._run_async(meta_cognition.self_monitor(conversations, period=period))
        return {"status": "success", "report": report.to_dict()}

    def get_meta_cognition_status(self) -> dict:
        """Return meta-cognition status summary."""
        meta_cognition = getattr(self, "meta_cognition", None)
        if not meta_cognition:
            return {"status": "disabled"}
        return {"status": "enabled", **meta_cognition.get_status()}

    def _collect_recent_conversations(self, limit: int = 120) -> list[dict]:
        """Collect recent conversation snippets for meta-cognition."""
        items = list(self.short_term_memory)[-limit:]
        conversations: list[dict] = []
        for item in items:
            conversations.append({"role": "user", "content": str(item)})
        return conversations

    def get_monitoring_status(self, lightweight: bool = False) -> dict[str, Any]:
        """Return memory subsystem monitoring snapshot.

        lightweight=True avoids persistence side effects (report/alert writes),
        intended for high-frequency MCP status polling.
        """
        return self.monitoring.status(persist_reports=not bool(lightweight))

    def get_advanced_insight_status(self) -> dict[str, Any]:
        """Return activation and recent output status for context/pattern analyzers."""
        context_count = 0
        pattern_count = 0
        if self.context_understanding is not None:
            try:
                context_count = len(getattr(self.context_understanding, "understandings", {}))
            except (RuntimeError, TypeError, ValueError, AttributeError) as exc:
                context_count = 0
                logger.debug("advanced_insight_status_context_count_failed: %s", exc)
        if self.pattern_recognition is not None:
            try:
                pattern_count = len(getattr(self.pattern_recognition, "patterns", {}))
            except (RuntimeError, TypeError, ValueError, AttributeError) as exc:
                pattern_count = 0
                logger.debug("advanced_insight_status_pattern_count_failed: %s", exc)
        return {
            "enabled": bool(self.context_understanding or self.pattern_recognition),
            "context_understanding_enabled": bool(self.context_understanding),
            "pattern_recognition_enabled": bool(self.pattern_recognition),
            "interaction_counter": int(self._advanced_insight_count),
            "context_records": int(context_count),
            "pattern_records": int(pattern_count),
        }

    def run_maintenance_now(self, force: bool = True) -> dict[str, Any]:
        """Run maintenance immediately (backup/sync/cleanup/restore drill)."""
        maintenance_result = self.maintenance.run_maintenance(force=force)
        policy_result = self._run_maintenance_policy(force=force)
        return {"maintenance": maintenance_result, "policy": policy_result}

    def run_restore_drill(self) -> dict[str, Any]:
        """Run one-click restore drill against latest backup."""
        return self.maintenance.run_restore_drill()

    def security_status(self) -> dict[str, Any]:
        """Get local encryption status."""
        return self.crypto.status()

    def security_enable(self, master_password: str) -> dict[str, Any]:
        """Enable optional local encryption and migrate protected plaintext files."""
        return self.crypto.enable_encryption(master_password)

    def security_disable(self, master_password: str) -> dict[str, Any]:
        """Disable local encryption and decrypt protected files back to plaintext."""
        return self.crypto.disable_encryption(master_password)

    def security_unlock(self, master_password: str) -> dict[str, Any]:
        """Unlock current process session for encrypted reads/writes."""
        ok = self.crypto.unlock(master_password)
        return {
            "status": "success" if ok else "error",
            "unlocked": ok,
            "status_view": self.crypto.status(),
        }

    def security_lock(self) -> dict[str, Any]:
        """Lock current process session."""
        self.crypto.lock()
        return {"status": "success", "status_view": self.crypto.status()}

    def security_recover(self, recovery_key: str, new_master_password: str) -> dict[str, Any]:
        """Recover encryption state using recovery key and rotate master password."""
        return recover_with_recovery_key(
            self.crypto, recovery_key=recovery_key, new_master_password=new_master_password
        )

    def shadow_status(self) -> dict[str, Any]:
        if not getattr(self, "shadow", None):
            return {"enabled": False}
        return self.shadow.status()

    def shadow_issue_token(
        self,
        caller_id: str,
        permissions: list[str],
        ttl_seconds: int = 1800,
    ) -> dict[str, Any]:
        if not getattr(self, "shadow", None):
            return {"status": "disabled"}
        perms = [str(x).strip() for x in (permissions or []) if str(x).strip()]
        if not perms:
            return {"status": "error", "reason": "permissions_required"}
        token = self.shadow.issue_capability_token(
            caller_id=str(caller_id or "trusted_cli"),
            permissions=perms,
            ttl_seconds=max(30, int(ttl_seconds)),
        )
        return {
            "status": "success",
            "caller_id": str(caller_id or "trusted_cli"),
            "permissions": perms,
            "ttl_seconds": max(30, int(ttl_seconds)),
            "token": token,
        }

    def shadow_revoke_token(self, token: str) -> dict[str, Any]:
        if not getattr(self, "shadow", None):
            return {"status": "disabled"}
        return self.shadow.revoke_capability_token(token)

    def shadow_seal(
        self,
        reason: str = "manual",
        level: str = "hard",
        *,
        caller_id: str = "memory_core",
        request_token: str = "",
        bypass_cooldown: bool = False,
    ) -> dict[str, Any]:
        if not getattr(self, "shadow", None):
            return {"enabled": False}
        return self.shadow.trigger_seal(
            reason=reason,
            level=level,
            caller_id=caller_id,
            request_token=request_token,
            bypass_cooldown=bool(bypass_cooldown),
        )

    def shadow_unseal(
        self,
        reason: str = "manual",
        *,
        caller_id: str = "memory_core",
        request_token: str = "",
        expected_seal_reason: str = "",
        expected_seal_session_id: str = "",
    ) -> dict[str, Any]:
        if not getattr(self, "shadow", None):
            return {"enabled": False}
        return self.shadow.clear_seal(
            reason=reason,
            caller_id=caller_id,
            request_token=request_token,
            expected_seal_reason=expected_seal_reason,
            expected_seal_session_id=expected_seal_session_id,
        )

    def shadow_health(self) -> dict[str, Any]:
        if not getattr(self, "shadow", None):
            return {"enabled": False}
        return self.shadow.health_check()

    def _shadow_write_func(self, text: str, source: str, recovery_meta: dict[str, Any] | None = None) -> None:
        # Keep recovery low-risk: save() path may itself re-enter shadow routing.
        meta = dict(recovery_meta or {})
        trust_marker = str(meta.get("trust_level", ""))
        if str(source).startswith("core.save"):
            safe = self._safe_text_for_memory_md(text)
            if not safe["allowed"]:
                return
            current = self.file_store.read_memory_md()
            stamp = self._utc_now().strftime("%Y-%m-%d %H:%M:%S")
            snippet = str(safe["text"])
            marker = f" [{trust_marker}]" if trust_marker else ""
            merged = f"{current}\n\n## Shadow Replay {stamp}{marker}\n{snippet}"
            self.file_store.write_memory_md(merged)
        else:
            marker = f"[{trust_marker}] " if trust_marker else ""
            self.file_store.append_to_daily_log(f"{marker}{str(text or '')}")
        try:
            self.whoosh_search.reindex_all()
        except (OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
            logger.debug("shadow_replay_reindex_failed: %s", exc)

    def _shadow_hash_exists_in_main(self, wanted_hash: str) -> bool:
        if not wanted_hash:
            return False
        h = str(wanted_hash)
        # 1) auto_memory records (best-effort)
        records_file = self.config["memory_dir"] / "auto_memory_records.jsonl"
        if records_file.exists():
            try:
                with records_file.open("r", encoding="utf-8") as f:
                    for line in f:
                        raw = line.strip()
                        if not raw:
                            continue
                        try:
                            obj = json.loads(raw)
                        except (TypeError, ValueError, json.JSONDecodeError) as exc:
                            logger.debug("shadow_hash_main_row_parse_failed: %s", exc)
                            continue
                        row_text = str(obj.get("normalized_text") or obj.get("text") or "")
                        if row_text and content_hash(row_text) == h:
                            return True
            except OSError as exc:
                logger.debug("shadow_hash_main_scan_failed: %s", exc)
        return False

    def shadow_replay_spool(
        self,
        *,
        caller_id: str = "memory_core",
        request_token: str = "",
    ) -> dict[str, Any]:
        if not getattr(self, "shadow", None):
            return {"status": "disabled"}
        if self.crypto.is_enabled() and (not self.crypto.is_unlocked()):
            return {"status": "blocked", "reason": "memory_security_locked"}
        self.shadow.bind_recovery_target("main_memory", self._shadow_write_func, self._shadow_hash_exists_in_main)
        return self.shadow.replay_spool(target="main_memory", caller_id=caller_id, request_token=request_token)

    def shadow_archive_spool(self) -> dict[str, Any]:
        if not getattr(self, "shadow", None):
            return {"status": "disabled"}
        return self.shadow.archive_replayed_spool()

    def shadow_startup_self_heal(self) -> dict[str, Any]:
        if not getattr(self, "shadow", None):
            return {"status": "disabled"}
        return self.shadow.startup_self_heal()

    def shadow_rotate_events_monthly(self) -> dict[str, Any]:
        if not getattr(self, "shadow", None):
            return {"status": "disabled"}
        return self.shadow.rotate_events_monthly()

    def shadow_sync_verified_backup(
        self,
        *,
        caller_id: str = "memory_core",
        request_token: str = "",
    ) -> dict[str, Any]:
        if not getattr(self, "shadow", None):
            return {"status": "disabled"}
        return self.shadow.sync_verified_backup(caller_id=caller_id, request_token=request_token)

    def shadow_recover_from_events(
        self,
        since_ts: str = "",
        *,
        caller_id: str = "memory_core",
        request_token: str = "",
    ) -> dict[str, Any]:
        if not getattr(self, "shadow", None):
            return {"status": "disabled"}
        if self.crypto.is_enabled() and (not self.crypto.is_unlocked()):
            return {"status": "blocked", "reason": "memory_security_locked"}
        self.shadow.bind_recovery_target("main_memory", self._shadow_write_func, self._shadow_hash_exists_in_main)
        return self.shadow.recover_from_events(
            target="main_memory",
            since_ts=str(since_ts or ""),
            caller_id=caller_id,
            request_token=request_token,
        )

    def shadow_verify(self) -> dict[str, Any]:
        if not getattr(self, "shadow", None):
            return {"status": "disabled"}
        return self.shadow.verify_checkpoints()

    def shadow_reset_checkpoint(self) -> dict[str, Any]:
        if not getattr(self, "shadow", None):
            return {"status": "disabled"}
        try:
            return self.shadow.reset_checkpoint()
        except (OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
            return {"status": "error", "error": str(exc)}

    def shadow_restore_snapshot(
        self,
        snapshot_path: str,
        *,
        caller_id: str = "memory_core",
        request_token: str = "",
    ) -> dict[str, Any]:
        if not getattr(self, "shadow", None):
            return {"status": "disabled"}
        return self.shadow.restore_shadow_snapshot(snapshot_path, caller_id=caller_id, request_token=request_token)

    def shadow_list_manifest_snapshots(self, limit: int = 20) -> dict[str, Any]:
        if not getattr(self, "shadow", None):
            return {"status": "disabled", "items": []}
        return {"status": "success", "items": self.shadow.list_manifest_snapshots(limit=limit)}

    def shadow_restore_manifest_snapshot(
        self,
        snapshot_path: str,
        *,
        caller_id: str = "memory_core",
        request_token: str = "",
    ) -> dict[str, Any]:
        if not getattr(self, "shadow", None):
            return {"status": "disabled"}
        return self.shadow.restore_manifest_snapshot(snapshot_path, caller_id=caller_id, request_token=request_token)

    def shadow_restore_backup_snapshot(
        self,
        backup_events_path: str,
        *,
        caller_id: str = "memory_core",
        request_token: str = "",
    ) -> dict[str, Any]:
        if not getattr(self, "shadow", None):
            return {"status": "disabled"}
        return self.shadow.restore_backup_snapshot(backup_events_path, caller_id=caller_id, request_token=request_token)

    def shadow_recovery_drill(
        self,
        *,
        caller_id: str = "trusted_cli",
        request_token: str = "",
        sample_text: str = "shadow_recovery_drill_sample",
    ) -> dict[str, Any]:
        if not getattr(self, "shadow", None):
            return {"status": "disabled"}
        return self.shadow.run_recovery_drill(
            caller_id=caller_id,
            request_token=request_token,
            sample_text=sample_text,
        )

    def repair_graph_access_counts(self, min_access: int = 1) -> dict[str, Any]:
        if not self._graph_enabled():
            return {"status": "disabled"}
        kg = self.knowledge_graph
        if kg is None:
            return {"status": "disabled"}
        return kg.backfill_entity_access_from_anchors(min_access=min_access)

    def purge_test_memory_data(self) -> dict[str, Any]:
        """Remove known test artifacts from working-memory persistence (non-destructive to core stores)."""
        result = {
            "status": "success",
            "working_memory": {"status": "skipped"},
            "auto_memory_pipeline": {"status": "skipped"},
        }
        try:
            result["working_memory"] = self.working_memory.purge_test_rows()
        except (OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
            result["working_memory"] = {"status": "error", "error": str(exc)}
            result["status"] = "partial"
        try:
            pipeline = getattr(self.auto_memory, "pipeline", None) if self.auto_memory else None
            if pipeline and hasattr(pipeline, "cleanup_test_pollution"):
                result["auto_memory_pipeline"] = pipeline.cleanup_test_pollution()
        except (OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
            result["auto_memory_pipeline"] = {"status": "error", "error": str(exc)}
            result["status"] = "partial"
        return result

    def backfill_auto_memory_record_ids(self) -> dict[str, Any]:
        memory_dir = Path(self.config["memory_dir"])
        records_file = memory_dir / "auto_memory_records.jsonl"
        if not records_file.exists():
            return {"status": "skipped", "reason": "records_missing"}
        rows = records_file.read_text(encoding="utf-8").splitlines()
        kept: list[str] = []
        updated = 0
        total = 0
        for line in rows:
            if not line.strip():
                continue
            total += 1
            try:
                row = json.loads(line)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                logger.debug("backfill_auto_memory_record_ids_skip_invalid_row: %s", exc)
                continue
            if not str(row.get("id", "")).strip():
                mid = str((row.get("meta") or {}).get("id", "")).strip()
                if mid:
                    row["id"] = mid
                    updated += 1
            kept.append(json.dumps(row, ensure_ascii=False))
        records_file.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
        normalize = normalize_memory_records(memory_dir)
        return {"status": "success", "total": total, "updated": updated, "normalize": normalize}

    def repair_semantic_cache(self, limit: int = 80) -> dict[str, Any]:
        try:
            return self.semantic_search.repair_missing_dense(limit=limit)
        except (OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
            return {"status": "error", "error": str(exc)}

    def rebalance_feedback_distribution(self, window: int | None = None) -> dict[str, Any]:
        """Build a recent-window effective feedback snapshot for tier/trust distribution diagnostics."""
        try:
            return self.knowledge_feedback.rebuild_balanced_feedback(window=window)
        except (OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
            return {"status": "error", "error": str(exc)}

    def generate_threshold_suggestions(
        self,
        window: int | None = None,
        enqueue_for_approval: bool = True,
        source: str = "manual",
    ) -> dict[str, Any]:
        """Generate threshold suggestions and enqueue for explicit human approval before apply."""
        try:
            wm = int(
                window
                or self.config["settings"]["memory"].get("maintenance_policy", {}).get("feedback_rebalance_window", 200)
            )
            report = self.knowledge_feedback.build_weekly_threshold_suggestion(window=wm)
            out = self.config["workspace_dir"] / "memory" / "threshold_suggestions_weekly.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(out, report, ensure_ascii=False, indent=2)
            if str(report.get("status", "")) != "success":
                return {**report, "output_file": str(out), "queued": False}
            suggestions = report.get("suggestions", []) if isinstance(report, dict) else []
            if not isinstance(suggestions, list) or not suggestions:
                return {
                    **report,
                    "output_file": str(out),
                    "queued": False,
                    "status": "success_no_suggestions",
                }
            if not enqueue_for_approval:
                return {**report, "output_file": str(out), "queued": False}
            pending = self._queue_threshold_suggestions(report, source=source)
            self._append_threshold_approval_log(
                {
                    "event": "queued",
                    "approval_id": pending.get("approval_id"),
                    "source": source,
                    "suggestions_count": len(pending.get("suggestions", [])),
                }
            )
            return {
                **report,
                "output_file": str(out),
                "queued": True,
                "approval_id": pending.get("approval_id"),
                "requires_confirmation": True,
                "status": "pending_approval",
            }
        except (OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
            return {"status": "error", "error": str(exc)}

    def list_pending_threshold_suggestions(self, include_processed: bool = False) -> dict[str, Any]:
        payload = self._load_threshold_pending()
        items = payload.get("items", []) if isinstance(payload, dict) else []
        if not include_processed:
            items = [x for x in items if str(x.get("status", "pending")) == "pending"]
        return {
            "status": "success",
            "pending_count": len(
                [
                    x
                    for x in (payload.get("items", []) if isinstance(payload, dict) else [])
                    if str(x.get("status", "pending")) == "pending"
                ]
            ),
            "items": items,
            "last_generated_at": payload.get("last_generated_at"),
            "last_applied_at": payload.get("last_applied_at"),
            "integrity_valid": bool(payload.get("_integrity_valid", True)),
        }

    def approve_threshold_suggestion(
        self,
        approval_id: str,
        approver: str = "manual",
        confirm: bool = False,
    ) -> dict[str, Any]:
        if not str(approval_id).strip():
            return {"status": "error", "error": "approval_id_required"}
        payload = self._load_threshold_pending()
        if not bool(payload.get("_integrity_valid", True)):
            self._append_threshold_approval_log(
                {
                    "event": "integrity_invalid",
                    "approval_id": approval_id,
                    "approver": approver,
                }
            )
            return {
                "status": "error",
                "error": "pending_suggestions_integrity_invalid",
                "approval_id": approval_id,
            }
        items = payload.get("items", []) if isinstance(payload, dict) else []
        target = None
        for row in items:
            if str(row.get("approval_id", "")) == str(approval_id):
                target = row
                break
        if target is None:
            return {"status": "error", "error": "approval_id_not_found", "approval_id": approval_id}
        if str(target.get("status", "pending")) != "pending":
            return {
                "status": "skipped",
                "reason": "already_processed",
                "approval_id": approval_id,
                "current_status": target.get("status"),
            }
        if not bool(confirm):
            return {
                "status": "requires_confirmation",
                "approval_id": approval_id,
                "message": "explicit_confirm_required",
                "preview": {
                    "stats": target.get("stats", {}),
                    "suggestions": target.get("suggestions", []),
                },
            }

        apply_result = self._apply_threshold_suggestions_to_workspace_config(target.get("suggestions", []))
        if str(apply_result.get("status", "")) != "success":
            self._append_threshold_approval_log(
                {
                    "event": "apply_failed",
                    "approval_id": approval_id,
                    "approver": approver,
                    "result": apply_result,
                }
            )
            return {"status": "error", "approval_id": approval_id, "apply_result": apply_result}

        target["status"] = "approved"
        target["approved_at"] = self._utc_now().isoformat()
        target["approved_by"] = approver
        target["apply_result"] = apply_result
        payload["last_applied_at"] = target["approved_at"]
        self._save_threshold_pending(payload)
        self._append_threshold_approval_log(
            {
                "event": "approved",
                "approval_id": approval_id,
                "approver": approver,
                "applied_count": len(apply_result.get("applied", [])),
            }
        )
        return {"status": "success", "approval_id": approval_id, "apply_result": apply_result}

    def reject_threshold_suggestion(
        self, approval_id: str, approver: str = "manual", reason: str = "manual_reject"
    ) -> dict[str, Any]:
        payload = self._load_threshold_pending()
        if not bool(payload.get("_integrity_valid", True)):
            self._append_threshold_approval_log(
                {
                    "event": "integrity_invalid_reject",
                    "approval_id": approval_id,
                    "approver": approver,
                }
            )
            return {
                "status": "error",
                "error": "pending_suggestions_integrity_invalid",
                "approval_id": approval_id,
            }
        items = payload.get("items", []) if isinstance(payload, dict) else []
        target = None
        for row in items:
            if str(row.get("approval_id", "")) == str(approval_id):
                target = row
                break
        if target is None:
            return {"status": "error", "error": "approval_id_not_found", "approval_id": approval_id}
        if str(target.get("status", "pending")) != "pending":
            return {
                "status": "skipped",
                "reason": "already_processed",
                "approval_id": approval_id,
                "current_status": target.get("status"),
            }
        target["status"] = "rejected"
        target["rejected_at"] = self._utc_now().isoformat()
        target["rejected_by"] = approver
        target["rejected_reason"] = reason
        self._save_threshold_pending(payload)
        self._append_threshold_approval_log(
            {
                "event": "rejected",
                "approval_id": approval_id,
                "approver": approver,
                "reason": reason,
            }
        )
        return {"status": "success", "approval_id": approval_id, "decision": "rejected"}

    def _threshold_guardrail_check(
        self,
        item: dict[str, Any],
        *,
        min_recent_count: int,
        max_suggestions_per_item: int,
        max_abs_delta: float,
        max_simple_top_k_delta: int,
    ) -> dict[str, Any]:
        stats = item.get("stats", {}) if isinstance(item.get("stats", {}), dict) else {}
        suggestions = item.get("suggestions", []) if isinstance(item.get("suggestions", []), list) else []
        reasons: list[str] = []
        recent_count = int(stats.get("recent_count", 0) or 0)
        if recent_count < max(1, int(min_recent_count)):
            reasons.append(f"recent_count_low:{recent_count}")
        if len(suggestions) > max(1, int(max_suggestions_per_item)):
            reasons.append(f"too_many_suggestions:{len(suggestions)}")
        for sug in suggestions:
            if not isinstance(sug, dict):
                reasons.append("invalid_suggestion_type")
                continue
            key = str(sug.get("key", "") or "")
            delta = sug.get("delta", 0)
            if not isinstance(delta, (int, float)):
                reasons.append(f"non_numeric_delta:{key}")
                continue
            is_simple_top_k = key.endswith("simple_top_k")
            if (not is_simple_top_k) and abs(float(delta)) > float(max_abs_delta):
                reasons.append(f"delta_too_large:{key}:{delta}")
            if is_simple_top_k and abs(float(delta)) > max(1, int(max_simple_top_k_delta)):
                reasons.append(f"simple_top_k_delta_too_large:{delta}")
        return {
            "pass": not reasons,
            "reasons": reasons,
            "recent_count": recent_count,
            "suggestion_count": len(suggestions),
        }

    def _backup_workspace_config_for_threshold_nav(self) -> Path | None:
        cfg_file = self.config["workspace_dir"] / "config.yaml"
        if not cfg_file.exists():
            return None
        backup_dir = self.config["memory_dir"] / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_file = backup_dir / f"config.threshold_nav.{self._utc_now().strftime('%Y%m%d%H%M%S')}.yaml"
        backup_file.write_text(cfg_file.read_text(encoding="utf-8"), encoding="utf-8")
        return backup_file

    def _restore_workspace_config_backup(self, backup_file: Path | None) -> dict[str, Any]:
        cfg_file = self.config["workspace_dir"] / "config.yaml"
        if backup_file is None or not backup_file.exists():
            return {"status": "skipped", "reason": "backup_not_found"}
        try:
            atomic_write_text(cfg_file, backup_file.read_text(encoding="utf-8"), encoding="utf-8")
            return {"status": "success", "restored_from": str(backup_file)}
        except OSError as exc:
            return {"status": "error", "error": str(exc), "restored_from": str(backup_file)}

    def auto_navigate_threshold_suggestions(self, limit: int | None = None) -> dict[str, Any]:
        cfg = self.config["settings"]["memory"].get("maintenance_policy", {})
        if not bool(cfg.get("threshold_auto_navigate_enabled", False)):
            return {"status": "disabled", "reason": "threshold_auto_navigate_disabled"}
        batch_limit = int(limit or cfg.get("threshold_auto_navigate_batch_limit", 1) or 1)
        min_recent = int(cfg.get("threshold_auto_navigate_min_recent_count", 80) or 80)
        max_sugs = int(cfg.get("threshold_auto_navigate_max_suggestions_per_item", 2) or 2)
        max_abs_delta = float(cfg.get("threshold_auto_navigate_max_abs_delta", 0.15) or 0.15)
        max_topk_delta = int(cfg.get("threshold_auto_navigate_max_simple_top_k_delta", 1) or 1)
        auto_reject = bool(cfg.get("threshold_auto_navigate_auto_reject_failed_guardrail", False))

        pending = self.list_pending_threshold_suggestions(include_processed=False)
        items = pending.get("items", []) if isinstance(pending.get("items", []), list) else []
        handled: list[dict[str, Any]] = []
        approved = 0
        for row in items[: max(1, batch_limit)]:
            approval_id = str(row.get("approval_id", "") or "")
            if not approval_id:
                continue
            check = self._threshold_guardrail_check(
                row,
                min_recent_count=min_recent,
                max_suggestions_per_item=max_sugs,
                max_abs_delta=max_abs_delta,
                max_simple_top_k_delta=max_topk_delta,
            )
            if not bool(check.get("pass", False)):
                decision = {
                    "approval_id": approval_id,
                    "status": "guardrail_blocked",
                    "guardrail": check,
                }
                self._append_threshold_approval_log(
                    {
                        "event": "auto_navigate_guardrail_blocked",
                        "approval_id": approval_id,
                        "guardrail": check,
                    }
                )
                if auto_reject:
                    rej = self.reject_threshold_suggestion(
                        approval_id=approval_id,
                        approver="maintenance_auto",
                        reason="auto_guardrail_blocked",
                    )
                    decision["auto_reject"] = rej
                handled.append(decision)
                continue

            backup_file = self._backup_workspace_config_for_threshold_nav()
            approve = self.approve_threshold_suggestion(
                approval_id=approval_id,
                approver="maintenance_auto",
                confirm=True,
            )
            if str(approve.get("status", "")) != "success":
                restore = self._restore_workspace_config_backup(backup_file)
                handled.append(
                    {
                        "approval_id": approval_id,
                        "status": "approve_failed",
                        "approve_result": approve,
                        "rollback": restore,
                    }
                )
                continue
            approved += 1
            handled.append(
                {
                    "approval_id": approval_id,
                    "status": "approved",
                    "guardrail": check,
                    "apply_result": approve.get("apply_result", {}),
                    "config_backup": str(backup_file) if backup_file else "",
                }
            )
            self._append_threshold_approval_log(
                {
                    "event": "auto_navigate_approved",
                    "approval_id": approval_id,
                    "applied_count": len((approve.get("apply_result", {}) or {}).get("applied", [])),
                }
            )
        return {
            "status": "success",
            "handled": handled,
            "approved_count": approved,
            "processed_count": len(handled),
            "pending_count": int(pending.get("pending_count", 0) or 0),
        }

    def is_learning_enabled(self) -> bool:
        """Check if active learning is enabled."""
        return self.config["settings"]["memory"]["learning"]["enabled"]

    def _build_llm_config_from_settings(self) -> LLMConfig:
        settings_obj = self.config.get("settings", {})
        mem_obj = settings_obj.get("memory", {}) if isinstance(settings_obj, dict) else {}
        mem = mem_obj if isinstance(mem_obj, dict) else {}
        llm_cfg = mem.get("llm", {}) if isinstance(mem.get("llm", {}), dict) else {}
        models = llm_cfg.get("models", {}) if isinstance(llm_cfg.get("models", {}), dict) else {}
        openai_cfg = llm_cfg.get("openai", {}) if isinstance(llm_cfg.get("openai", {}), dict) else {}
        openrouter_cfg = llm_cfg.get("openrouter", {}) if isinstance(llm_cfg.get("openrouter", {}), dict) else {}
        task_order_raw = llm_cfg.get("task_provider_order", {})
        task_provider_order: dict[str, tuple[str, ...]] = {}
        if isinstance(task_order_raw, dict):
            for k, v in task_order_raw.items():
                if isinstance(v, list):
                    task_provider_order[str(k)] = tuple(str(x) for x in v if str(x).strip())
        return LLMConfig(
            primary_model=str(models.get("primary_model", "gemma3:1b")),
            complex_model=str(models.get("complex_model", "llama3.2:3b")),
            reasoning_model=str(models.get("reasoning_model", "llama3.2:3b")),
            embedding_model=str(models.get("embedding_model", "nomic-embed-text:latest")),
            provider_order_chat=tuple(
                str(x)
                for x in llm_cfg.get("provider_order_chat", ["ollama", "openai", "openrouter"])
            ),
            provider_order_embedding=tuple(
                str(x) for x in llm_cfg.get("provider_order_embedding", ["ollama", "openai", "openrouter"])
            ),
            task_provider_order=task_provider_order,
            llm_timeout_seconds=int(llm_cfg.get("timeout_seconds", 25) or 25),
            failover_enabled=bool(llm_cfg.get("failover_enabled", True)),
            failover_max_errors=int(llm_cfg.get("failover_max_errors", 3) or 3),
            failover_cooldown_seconds=int(llm_cfg.get("failover_cooldown_seconds", 120) or 120),
            openai_enabled=bool(openai_cfg.get("enabled", True)),
            openai_base_url=str(openai_cfg.get("base_url", "https://api.openai.com/v1")),
            openai_api_key=str(openai_cfg.get("api_key", "")),
            openai_chat_model=str(openai_cfg.get("chat_model", "gpt-4.1-mini")),
            openai_embedding_model=str(openai_cfg.get("embedding_model", "text-embedding-3-small")),
            openrouter_enabled=bool(openrouter_cfg.get("enabled", True)),
            openrouter_base_url=str(openrouter_cfg.get("base_url", "https://openrouter.ai/api/v1")),
            openrouter_api_key=str(openrouter_cfg.get("api_key", "")),
            openrouter_chat_model=str(openrouter_cfg.get("chat_model", "openai/gpt-4.1-mini")),
            openrouter_embedding_model=str(openrouter_cfg.get("embedding_model", "openai/text-embedding-3-small")),
        )
