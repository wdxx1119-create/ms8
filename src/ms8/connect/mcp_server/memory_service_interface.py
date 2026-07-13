from __future__ import annotations

import json
import logging
import os
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, cast

from ...engine import MemoryCoreEngine
from ...engine_core.core import MemoryCore
from ...engine_core.expression_preference_profile import (
    load_conversation_state,
    load_expression_profile,
    prepare_profile_for_round,
    resolve_expression_profile_dir,
    save_conversation_state,
    save_expression_profile,
    update_conversation_state_with_policy,
    update_profile_from_decision,
)
from ...engine_core.response_mode_router import choose_cognitive_phrase, route_response
from ...engine_core.response_mode_types import RouterDecision
from ...engine_core.sticky_prompt_templates import GUARDRAIL_PROMPT_EXTRA, build_profile_hint, get_prompt_extra
from ...memory.compat import (
    LedgerCompatibilityError,
    LedgerMemoryCompatibilityAdapter,
    build_ledger_memory_compatibility_adapter,
)
from ...paths import get_ms8_home
from ..integration_hooks.service_models import MemoryCandidate
from .memory_access_policy import memory_row_browsable, redact_memory_row

ERR_CORE_UNAVAILABLE = "E_CORE_UNAVAILABLE"
ERR_PROFILE_NOT_FOUND = "E_PROFILE_NOT_FOUND"
ERR_PROFILE_PARSE = "E_PROFILE_PARSE"
ERR_PROFILE_UNKNOWN = "E_PROFILE_UNKNOWN"

logger = logging.getLogger(__name__)
MAX_PAGE_SIZE = 500
DEFAULT_PAGE_SIZE = 100


@dataclass
class MemoryServiceInterface:
    config: dict[str, Any]
    core: MemoryCore | None = None
    core_error: str = ""
    ledger_adapter: LedgerMemoryCompatibilityAdapter | None = None
    ledger_requested: bool = False
    ledger_error: str = ""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        core: MemoryCore | None = None,
        core_error: str = "",
        ledger_adapter: LedgerMemoryCompatibilityAdapter | None = None,
        ledger_requested: bool = False,
        ledger_error: str = "",
    ) -> None:
        self.config = config if isinstance(config, dict) else {}
        self.core = core
        self.core_error = str(core_error or "")
        self.ledger_adapter = ledger_adapter
        self.ledger_requested = bool(ledger_requested)
        self.ledger_error = str(ledger_error or "")

    @classmethod
    def from_config(cls, cfg: dict[str, Any] | None) -> MemoryServiceInterface:
        config = cfg if isinstance(cfg, dict) else {}
        core: MemoryCore | None = None
        core_error = ""
        try:
            workspace = cls._resolve_workspace(
                str(
                    (
                        config.get("memory_core", {})
                        if isinstance(config.get("memory_core", {}), dict)
                        else {}
                    ).get("workspace", os.environ.get("MS8_HOME", str(get_ms8_home())))
                )
            )
            os.environ["OPENCLAW_MEMORY_WORKSPACE"] = str(workspace)
            core = MemoryCore(llm_enabled=False)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:  # pragma: no cover
            core_error = str(exc)
        ledger_cfg = config.get("memory_ledger_v1", {})
        ledger_requested = isinstance(ledger_cfg, dict) and ledger_cfg.get("enabled") is True
        ledger_adapter: LedgerMemoryCompatibilityAdapter | None = None
        ledger_error = ""
        if ledger_requested:
            try:
                workspace = cls._resolve_workspace(
                    str(
                        (
                            config.get("memory_core", {})
                            if isinstance(config.get("memory_core", {}), dict)
                            else {}
                        ).get("workspace", os.environ.get("MS8_HOME", str(get_ms8_home())))
                    )
                )
                ledger_adapter = build_ledger_memory_compatibility_adapter(config, workspace)
            except (LedgerCompatibilityError, OSError, RuntimeError, TypeError, ValueError) as exc:
                ledger_error = str(exc)
        return cls(
            config=config,
            core=core,
            core_error=core_error,
            ledger_adapter=ledger_adapter,
            ledger_requested=ledger_requested,
            ledger_error=ledger_error,
        )

    @staticmethod
    def _expand(raw: str) -> Path:
        return Path(str(raw or str(get_ms8_home()))).expanduser()

    @classmethod
    def _resolve_workspace(cls, raw: str) -> Path:
        candidate = cls._expand(raw)
        if cls._is_writable_dir(candidate):
            return candidate
        fallback = (Path.cwd() / ".ms8").resolve()
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback

    @staticmethod
    def _is_writable_dir(path: Path) -> bool:
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return True
        except OSError:
            return False

    def _workspace(self) -> Path:
        core_cfg = self.config.get("memory_core", {}) if isinstance(self.config.get("memory_core", {}), dict) else {}
        return self._resolve_workspace(str(core_cfg.get("workspace", os.environ.get("MS8_HOME", str(get_ms8_home())))))

    def _engine_adapter(self) -> MemoryCoreEngine:
        return MemoryCoreEngine(self._workspace())

    def available(self) -> bool:
        return self.core is not None or self.ledger_adapter is not None

    def _core_unavailable(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": f"MemoryCore unavailable: {self.core_error}",
            "error_code": ERR_CORE_UNAVAILABLE,
        }

    def _ledger_unavailable(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": f"Ledger-v1 compatibility unavailable: {self.ledger_error}",
            "error_code": "E_LEDGER_V1_UNAVAILABLE",
        }

    def submit(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.ledger_requested:
            if self.ledger_adapter is None:
                unavailable = self._ledger_unavailable()
                unavailable["accepted"] = False
                return unavailable
            return {
                "ok": False,
                "accepted": False,
                "error": "ledger_v1_write_not_enabled",
                "error_code": "E_LEDGER_V1_WRITE_NOT_ENABLED",
            }
        if not self.available():
            return self._core_unavailable()
        candidate = MemoryCandidate.from_payload(payload)
        if not candidate.content:
            return {"ok": False, "accepted": False, "error": "empty_content"}
        assert self.core is not None
        try:
            if hasattr(self.core, "write_gateway"):
                result = self.core.write_gateway(
                    candidate.content,
                    source=candidate.source,
                    category=candidate.category,
                    write_daily_log=True,
                )
            else:  # pragma: no cover
                self.core.append_interaction(candidate.content)
                result = {"status": "saved"}
            return {
                "ok": True,
                "accepted": True,
                "result": self._json_safe(result),
                "candidate": {
                    "source": candidate.source,
                    "category": candidate.category,
                },
            }
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning("mcp_submit_failed err=%s", exc)
            return {"ok": False, "accepted": False, "error": str(exc), "error_code": "E_MCP_SUBMIT_FAILED"}

    def pre_action_check(
        self,
        action: str,
        *,
        memory_ids: list[str] | None = None,
        explicit_user_confirmation: bool = False,
    ) -> dict[str, Any]:
        return self._engine_adapter().pre_action_check(
            action,
            memory_ids=memory_ids,
            explicit_user_confirmation=explicit_user_confirmation,
        )

    def query(
        self,
        text: str,
        top_k: int = 5,
        *,
        recorded_as_of: str | None = None,
        valid_at: str | None = None,
        realm_id: str | None = None,
        scope: str | None = None,
    ) -> dict[str, Any]:
        query = str(text or "").strip()
        if self.ledger_requested:
            if self.ledger_adapter is None:
                return self._ledger_unavailable()
            try:
                return self.ledger_adapter.query(
                    query,
                    top_k,
                    recorded_as_of=recorded_as_of,
                    valid_at=valid_at,
                    realm_id=realm_id,
                    scope=scope,
                )
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                logger.warning("ledger_v1_query_failed query=%s err=%s", query, exc)
                return {
                    "ok": False,
                    "query": query,
                    "error": str(exc),
                    "error_code": "E_LEDGER_V1_QUERY_FAILED",
                    "results": [],
                }
        if not self.available():
            return self._core_unavailable()
        try:
            gateway = self._engine_adapter().retrieve_gateway(
                query=query,
                limit=int(max(1, top_k)),
                purpose="recall",
                allow_semantic=False,
                allow_graph=False,
            )
            rows = gateway.get("items", []) if isinstance(gateway.get("items", []), list) else []
            normalized = [self._normalize_result_row(r) for r in rows if isinstance(r, dict)]
            return {
                "ok": True,
                "query": query,
                "count": len(normalized),
                "results": normalized,
                "retrieval_gateway": gateway.get("trace", {}),
            }
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning("mcp_query_failed query=%s err=%s", query, exc)
            return {
                "ok": False,
                "query": query,
                "error": str(exc),
                "error_code": "E_MCP_QUERY_FAILED",
                "results": [],
            }

    def memory_catalog(self, *, include_blocked: bool = False) -> dict[str, Any]:
        rows = self._read_memory_rows(include_blocked=include_blocked)
        latest_created_at = max((str(row.get("created_at", "")) for row in rows), default="")
        return {
            "ok": True,
            "provider": "ms8_runtime",
            "read_only": True,
            "audit_view": bool(include_blocked),
            "total": len(rows),
            "sources": dict(sorted(Counter(str(row.get("source", "")) for row in rows if row.get("source")).items())),
            "categories": dict(
                sorted(Counter(str(row.get("category", "")) for row in rows if row.get("category")).items())
            ),
            "statuses": dict(sorted(Counter(str(row.get("status", "")) for row in rows if row.get("status")).items())),
            "latest_created_at": latest_created_at,
        }

    def memory_list(
        self,
        *,
        offset: int = 0,
        limit: int = DEFAULT_PAGE_SIZE,
        view: str = "summary",
        source: str = "",
        category: str = "",
        status: str = "",
        include_blocked: bool = False,
    ) -> dict[str, Any]:
        offset, limit = self._validated_page(offset, limit)
        rows = self._filter_memory_rows(
            self._read_memory_rows(include_blocked=include_blocked),
            source=source,
            category=category,
            status=status,
        )
        page = rows[offset : offset + limit]
        next_offset = offset + len(page)
        return {
            "ok": True,
            "provider": "ms8_runtime",
            "audit_view": bool(include_blocked),
            "view": view,
            "offset": offset,
            "limit": limit,
            "total": len(rows),
            "next_offset": next_offset if next_offset < len(rows) else None,
            "items": [self._render_memory_row(row, view) for row in page],
        }

    def memory_get(
        self,
        memory_id: str,
        *,
        view: str = "full",
        include_blocked: bool = False,
    ) -> dict[str, Any]:
        normalized_id = str(memory_id or "").strip()
        if not normalized_id:
            return {"ok": False, "status": "invalid_request", "reason": "memory_id_required"}
        for row in self._read_memory_rows(include_blocked=include_blocked):
            if str(row.get("id", "")).strip() == normalized_id:
                return {
                    "ok": True,
                    "provider": "ms8_runtime",
                    "audit_view": bool(include_blocked),
                    "item": self._render_memory_row(row, view),
                }
        return {
            "ok": False,
            "status": "not_found",
            "reason": "memory_not_found",
            "memory_id": normalized_id,
        }

    def memory_search(self, query: str, *, limit: int = 20, view: str = "summary") -> dict[str, Any]:
        text = str(query or "").strip()
        if not text:
            return {"ok": False, "status": "invalid_request", "reason": "query_required", "items": []}
        try:
            gateway = self._engine_adapter().retrieve_gateway(
                query=text,
                limit=int(max(1, min(limit, MAX_PAGE_SIZE))),
                purpose="recall",
                allow_semantic=False,
                allow_graph=False,
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning("mcp_memory_search_failed query=%s err=%s", text, exc)
            return {
                "ok": False,
                "status": "error",
                "reason": str(exc),
                "error_code": "E_MCP_MEMORY_SEARCH_FAILED",
                "items": [],
            }
        rows = gateway.get("items", []) if isinstance(gateway.get("items", []), list) else []
        items = [self._render_memory_row(row, view) for row in rows if isinstance(row, dict)]
        return {
            "ok": True,
            "provider": "ms8_runtime",
            "query": text,
            "limit": int(max(1, min(limit, MAX_PAGE_SIZE))),
            "total_matches": len(items),
            "items": items,
            "retrieval_gateway": gateway.get("trace", {}),
        }

    def context(
        self,
        text: str,
        limit: int = 5,
        *,
        recorded_as_of: str | None = None,
        valid_at: str | None = None,
        realm_id: str | None = None,
        scope: str | None = None,
    ) -> dict[str, Any]:
        query = str(text or "").strip()
        if self.ledger_requested:
            if self.ledger_adapter is None:
                return self._ledger_unavailable()
            try:
                out = self.ledger_adapter.context(
                    query,
                    limit,
                    recorded_as_of=recorded_as_of,
                    valid_at=valid_at,
                    realm_id=realm_id,
                    scope=scope,
                )
                out["recommended_actions"] = self._recommended_actions_from_query(query)
                return out
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                logger.warning("ledger_v1_context_failed query=%s err=%s", query, exc)
                return {
                    "ok": False,
                    "query": query,
                    "error": str(exc),
                    "error_code": "E_LEDGER_V1_CONTEXT_FAILED",
                    "context": {},
                }
        if not self.available():
            return self._core_unavailable()
        assert self.core is not None
        try:
            payload = self.core.get_response_memory_context(query)
            out = self._json_safe(payload)
            if isinstance(out, dict) and isinstance(out.get("memories"), list):
                out["memories"] = out["memories"][: int(max(1, limit))]
            expression = {}
            if isinstance(out, dict) and isinstance(out.get("expression_mode"), dict):
                expression = dict(out.get("expression_mode", {}))
                if "decision" not in expression and "mode" in expression:
                    expression = {
                        "decision": expression,
                        "mode": expression.get("mode"),
                        "confidence": expression.get("confidence", 1.0),
                    }
                if "prompt_extra" not in expression:
                    mode = str(expression.get("mode") or (expression.get("decision", {}) or {}).get("mode") or "normal")
                    mode_value: Literal["normal", "light", "strong"] = cast(
                        Literal["normal", "light", "strong"],
                        mode if mode in {"normal", "light", "strong"} else "normal",
                    )
                    prompt_extra = get_prompt_extra(mode_value)
                    if GUARDRAIL_PROMPT_EXTRA:
                        prompt_extra = (
                            f"{prompt_extra}\n\n{GUARDRAIL_PROMPT_EXTRA}".strip()
                            if prompt_extra
                            else GUARDRAIL_PROMPT_EXTRA
                        )
                    expression["prompt_extra"] = prompt_extra
            if not expression:
                expression = self._build_expression_context(query, out)
            prompt_extra = str(expression.get("prompt_extra", "") or "")
            if isinstance(out, dict):
                out.setdefault("system_prompt_extra", prompt_extra)
                if "context_with_expression" not in out:
                    context_text = str(out.get("context", "") or "")
                    if prompt_extra and context_text:
                        out["context_with_expression"] = (
                            f"[SYSTEM_PROMPT_EXTRA]\n{prompt_extra}\n\n[MEMORY_CONTEXT]\n{context_text}"
                        )
                    elif prompt_extra:
                        out["context_with_expression"] = f"[SYSTEM_PROMPT_EXTRA]\n{prompt_extra}"
                    else:
                        out["context_with_expression"] = context_text
            recommended_actions = self._recommended_actions_from_query(query)
            retrieval_gateway = {}
            if isinstance(out, dict) and isinstance(out.get("retrieval_gateway"), dict):
                retrieval_gateway = dict(out.get("retrieval_gateway", {}))
            return {
                "ok": True,
                "query": query,
                "context": out,
                "retrieval_gateway": retrieval_gateway,
                "expression_mode": expression,
                "system_prompt_extra": prompt_extra,
                "context_with_expression": (out.get("context_with_expression") if isinstance(out, dict) else ""),
                "recommended_actions": recommended_actions,
            }
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning("mcp_context_failed query=%s err=%s", query, exc)
            return {
                "ok": False,
                "query": query,
                "error": str(exc),
                "error_code": "E_MCP_CONTEXT_FAILED",
                "context": {},
            }

    def ledger_explain(self, claim_id: str) -> dict[str, Any]:
        normalized = str(claim_id or "").strip()
        if not self.ledger_requested:
            return {
                "ok": False,
                "status": "unsupported",
                "reason": "ledger_v1_not_selected",
                "error_code": "E_LEDGER_V1_NOT_SELECTED",
            }
        if self.ledger_adapter is None:
            return self._ledger_unavailable()
        try:
            return self.ledger_adapter.explain(normalized)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning("ledger_v1_explain_failed claim_id=%s err=%s", normalized, exc)
            return {
                "ok": False,
                "status": "error",
                "reason": str(exc),
                "error_code": "E_LEDGER_V1_EXPLAIN_FAILED",
                "claim_id": normalized,
            }

    def _recommended_actions_from_query(self, query: str) -> list[dict[str, Any]]:
        text = str(query or "").strip()
        low = text.lower()
        actions: list[dict[str, Any]] = [
            {
                "tool": "prepare_reply",
                "reason": "use_prepare_reply_before_answer_for_consistent_memory_read",
            }
        ]
        if not text:
            return actions
        signal_keywords = (
            "喜欢",
            "偏好",
            "习惯",
            "决定",
            "选择",
            "规则",
            "约束",
            "不对",
            "应该",
            "记住",
            "经验",
            "教训",
            "preference",
            "decision",
            "constraint",
            "feedback",
            "lesson",
        )
        if any(k in text for k in signal_keywords) or any(k in low for k in signal_keywords):
            actions.append(
                {
                    "tool": "submit",
                    "reason": "high_value_memory_signal_detected",
                    "hint": "Persist at least one durable memory from this turn.",
                }
            )
            actions.append(
                {
                    "tool": "batch_submit",
                    "reason": "multiple_memory_candidates_possible",
                    "hint": "Use batch_submit if more than one durable fact/preference/decision is present.",
                }
            )
        return actions

    def _build_expression_context(self, query: str, context_payload: Any) -> dict[str, Any]:
        workspace = self._workspace()
        memory_dir = resolve_expression_profile_dir(workspace / "memory")
        recent_summary = ""
        if isinstance(context_payload, dict):
            recent_summary = str(context_payload.get("memory_context", "") or context_payload.get("summary", "") or "")
        try:
            state = load_conversation_state(memory_dir)
            profile = load_expression_profile(memory_dir)
            current_round = int(state.current_round) + 1
            router_cfg = self._router_config()
            decay = (
                router_cfg.get("profile", {}).get("decay", 0.95)
                if isinstance(router_cfg.get("profile", {}), dict)
                else 0.95
            )
            prepared_profile, valid_profile = prepare_profile_for_round(
                profile,
                current_round=current_round,
                decay=float(decay),
            )
            decision = route_response(
                user_message=query,
                recent_summary=recent_summary,
                profile=prepared_profile if valid_profile else None,
                conversation_state=state,
                router_config=router_cfg,
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
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
                cooldown_cfg = self._router_config().get("cooldown", {})
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
        except (OSError, TypeError, ValueError) as exc:
            logger.warning("expression_profile_update_failed err=%s", exc)

        return {
            "mode": decision.mode,
            "confidence": decision.confidence,
            "prompt_extra": prompt_extra,
            "decision": decision.to_dict(),
        }

    def _router_config(self) -> dict[str, Any]:
        cfg_obj = {}
        try:
            if hasattr(self.core, "config") and isinstance(getattr(self.core, "config"), dict):
                cfg_obj = getattr(self.core, "config")
        except (AttributeError, TypeError) as exc:
            logger.debug("Failed to read router config from core: %s", exc)
            cfg_obj = {}
        settings = cfg_obj.get("settings", {}) if isinstance(cfg_obj, dict) else {}
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

    def status(self) -> dict[str, Any]:
        if not self.available():
            return self._core_unavailable()
        assert self.core is not None
        health = {}
        if hasattr(self.core, "get_monitoring_status"):
            try:
                health = self.core.get_monitoring_status(lightweight=True)
            except TypeError:
                # Backward-compatible for older/mocked cores that only accept no-arg calls.
                health = self.core.get_monitoring_status()
        return {
            "ok": True,
            "service": "ms8-connect",
            "healthy": True,
            "time": datetime.now(timezone.utc).isoformat(),
            "health": self._json_safe(health),
        }

    def quick_status(self) -> dict[str, Any]:
        """Side-effect free status for MCP high-frequency polling."""
        if not self.available():
            return self._core_unavailable()
        workspace = self._workspace()
        memory_dir = workspace / "memory"
        return {
            "ok": True,
            "service": "ms8-connect",
            "healthy": True,
            "time": datetime.now(timezone.utc).isoformat(),
            "workspace": str(workspace),
            "memory_dir_exists": memory_dir.exists(),
            "core_available": True,
        }

    def profile(self, key: str) -> dict[str, Any]:
        resource = str(key or "").strip().lower()
        workspace = self._workspace()
        try:
            if resource == "long-term":
                p = workspace / "MEMORY.md"
                if not p.exists():
                    return self._profile_error(resource, "MEMORY.md not found", ERR_PROFILE_NOT_FOUND)
                content = p.read_text(encoding="utf-8", errors="ignore")[-12000:]
                return self._profile_ok(resource, content, p)
            if resource == "profile":
                p = workspace / "memory" / "memory_blocks.json"
                if not p.exists():
                    return self._profile_error(resource, "profile not found", ERR_PROFILE_NOT_FOUND)
                obj = json.loads(p.read_text(encoding="utf-8"))
                return self._profile_ok(resource, obj, p)
            if resource == "recent":
                p = workspace / "memory" / "auto_memory_records.jsonl"
                if not p.exists():
                    return self._profile_error(resource, "recent memory not found", ERR_PROFILE_NOT_FOUND)
                rows = []
                for line in p.read_text(encoding="utf-8", errors="ignore").splitlines()[-30:]:
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError as exc:
                        logger.debug("Failed to parse recent profile JSONL row: %s", exc)
                        continue
                    rows.append(self._normalize_result_row(obj))
                return self._profile_ok(resource, rows, p)
            return self._profile_error(resource, "unknown profile key", ERR_PROFILE_UNKNOWN)
        except json.JSONDecodeError as exc:
            return self._profile_error(resource, f"profile parse error: {exc}", ERR_PROFILE_PARSE)
        except OSError as exc:
            return self._profile_error(resource, str(exc), ERR_PROFILE_PARSE)

    def _profile_ok(self, resource_key: str, content: Any, path: Path) -> dict[str, Any]:
        return {
            "ok": True,
            "resource": resource_key,
            "content": self._json_safe(content),
            "path": str(path),
        }

    def _profile_error(self, resource_key: str, error: str, error_code: str) -> dict[str, Any]:
        return {
            "ok": False,
            "resource": resource_key,
            "error": error,
            "error_code": error_code,
            "content": error,
        }

    def _resource_int(self, key: str, default: int) -> int:
        resources = self.config.get("resources", {}) if isinstance(self.config.get("resources", {}), dict) else {}
        raw = resources.get(str(key), default)
        try:
            return int(raw)
        except (TypeError, ValueError) as exc:
            logger.debug("Failed to parse resource int '%s': %s", key, exc)
            return int(default)

    @staticmethod
    def _validated_page(offset: int, limit: int) -> tuple[int, int]:
        if offset < 0:
            raise ValueError("offset must be non-negative")
        if not 1 <= limit <= MAX_PAGE_SIZE:
            raise ValueError(f"limit must be between 1 and {MAX_PAGE_SIZE}")
        return offset, limit


    @staticmethod
    def _read_all_memory_rows(adapter: MemoryCoreEngine) -> list[dict[str, Any]]:
        records_file = adapter.records_file()
        if not records_file.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in records_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                logger.debug("skip_invalid_mcp_memory_json err=%s", exc)
                continue
            if isinstance(payload, dict):
                if "text" not in payload and "normalized_text" in payload:
                    payload["text"] = payload.get("normalized_text", "")
                rows.append(payload)
        return rows

    def _read_memory_rows(self, *, include_blocked: bool = False) -> list[dict[str, Any]]:
        adapter = self._engine_adapter()
        source_rows = self._read_all_memory_rows(adapter) if include_blocked else adapter.read_memories()
        rows = [dict(row) for row in source_rows if isinstance(row, dict)]
        if include_blocked:
            return rows
        return [row for row in rows if memory_row_browsable(row)]

    @staticmethod
    def _filter_memory_rows(
        rows: list[dict[str, Any]],
        *,
        source: str = "",
        category: str = "",
        status: str = "",
    ) -> list[dict[str, Any]]:
        source_filter = str(source or "").strip()
        category_filter = str(category or "").strip()
        status_filter = str(status or "").strip()
        out: list[dict[str, Any]] = []
        for row in rows:
            if source_filter and str(row.get("source", "")) != source_filter:
                continue
            if category_filter and str(row.get("category", "")) != category_filter:
                continue
            if status_filter and str(row.get("status", "")) != status_filter:
                continue
            out.append(row)
        return out


    def _render_memory_row(self, row: dict[str, Any], view: str) -> dict[str, Any]:
        safe_row = redact_memory_row(row)
        if view == "summary":
            return self._normalize_result_row(safe_row)
        if view == "full":
            return self._json_safe(safe_row)
        raise ValueError("view must be 'summary' or 'full'")

    def _normalize_submit_result(self, raw: Any) -> dict[str, Any]:
        items = raw if isinstance(raw, list) else [raw]
        accepted = 0
        review = 0
        rejected = 0
        normalized: list[dict[str, Any]] = []
        for item in items:
            obj = item if isinstance(item, dict) else {}
            status = str(obj.get("status") or obj.get("result", {}).get("status", "")).strip().lower()
            if status in {"saved", "success", "accepted"}:
                accepted += 1
            elif status in {"pending_review", "saved_review", "queued_review", "redacted_accept"}:
                review += 1
            elif status:
                rejected += 1
            normalized.append(self._json_safe(obj))
        return {
            "items": normalized,
            "accepted_count": accepted,
            "review_count": review,
            "rejected_count": rejected,
            "saved_any": bool(accepted or review),
        }

    @staticmethod
    def _normalize_result_row(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(row.get("id", "")),
            "text": str(row.get("text") or row.get("normalized_text") or ""),
            "source": str(row.get("source", "")),
            "category": str(row.get("category", "")),
            "status": str(row.get("status", "")),
            "created_at": str(row.get("created_at", "")),
        }

    @staticmethod
    def _json_safe(payload: Any) -> Any:
        def _coerce(obj: Any) -> Any:
            if obj is None or isinstance(obj, (str, int, float, bool)):
                return obj
            if isinstance(obj, dict):
                return {str(k): _coerce(v) for k, v in obj.items()}
            if isinstance(obj, (list, tuple)):
                return [_coerce(v) for v in obj]
            if isinstance(obj, set):
                return [_coerce(v) for v in obj]
            return str(obj)

        safe = _coerce(payload)
        try:
            json.dumps(safe, ensure_ascii=False)
            return safe
        except (TypeError, ValueError, OverflowError) as exc:
            logger.debug("Failed to json-serialize normalized payload: %s", exc)
            return str(safe)
