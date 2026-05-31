"""Open backend adapter for policy-engine interface."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from typing import Any

from .policy_engine_iface import PolicyEnvelope


def _trace_id(payload: Mapping[str, Any]) -> str:
    raw = repr(sorted((str(k), repr(v)) for k, v in payload.items()))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _ok(data: dict[str, Any], payload: Mapping[str, Any], code: str = "OK") -> PolicyEnvelope:
    return {
        "ok": True,
        "code": code,
        "reason": "open_backend",
        "trace_id": _trace_id(payload),
        "data": data,
    }


class OpenPolicyEngine:
    """Open implementation with conservative policy behavior."""

    backend_name = "open"
    backend_version = "1.0"

    def evaluate_admission(self, payload: Mapping[str, Any]) -> PolicyEnvelope:
        text = str(payload.get("text", "")).strip()
        if not text:
            return _ok(
                {
                    "route": "rejected",
                    "reasons": ["empty_text"],
                    "normalized_text": "",
                    "should_persist_main": False,
                    "should_index": False,
                    "should_write_memory_md": False,
                    "redacted": False,
                    "replace_old": False,
                    "privacy_flags": [],
                    "conflict_flags": [],
                    "risk_scores": {"noise": 1.0, "privacy": 0.0, "conflict": 0.0, "memory_value": 0.0},
                    "raw": {"open_backend": True},
                },
                payload,
            )

        normalized = text
        reasons: list[str] = []
        route = "accepted"
        should_persist_main = True
        should_index = True
        should_write_memory_md = True
        redacted = False
        replace_old = False
        privacy_flags: list[str] = []
        conflict_flags: list[str] = []

        if self._looks_like_noise(text):
            route = "short_term_only"
            reasons.append("noise_low_value")
            should_persist_main = False
            should_index = False
            should_write_memory_md = False

        redacted_text, privacy_flags = self._redact_sensitive(text)
        if privacy_flags:
            reasons.append("privacy_hit")
            normalized = redacted_text
            redacted = normalized != text
            if "credential_high_risk" in privacy_flags:
                route = "pending_review"
                should_index = False
                should_write_memory_md = False
            elif route != "short_term_only":
                route = "redacted_accept"
                should_persist_main = True
                should_index = True
                should_write_memory_md = True

        if self._looks_like_conflict(text):
            conflict_flags.append("possible_conflict")
            reasons.append("conflict_signal")
            if route not in {"rejected", "short_term_only"}:
                route = "pending_review"
                should_index = False
                should_write_memory_md = False

        if not reasons:
            reasons.append("open_policy_accept")

        risk_scores = {
            "noise": 0.8 if route in {"short_term_only", "rejected"} else 0.2,
            "privacy": 1.0 if privacy_flags else 0.0,
            "conflict": 0.7 if conflict_flags else 0.0,
            "memory_value": 0.8 if route in {"accepted", "redacted_accept"} else 0.3,
        }
        return _ok(
            {
                "route": route,
                "reasons": reasons,
                "normalized_text": normalized,
                "should_persist_main": should_persist_main,
                "should_index": should_index,
                "should_write_memory_md": should_write_memory_md,
                "redacted": redacted,
                "replace_old": replace_old,
                "privacy_flags": privacy_flags,
                "conflict_flags": conflict_flags,
                "risk_scores": risk_scores,
                "raw": {"open_backend": True},
            },
            payload,
        )

    async def aevaluate_admission(self, payload: Mapping[str, Any]) -> PolicyEnvelope:
        return self.evaluate_admission(payload)

    def rank_retrieval(self, payload: Mapping[str, Any]) -> PolicyEnvelope:
        candidates = payload.get("candidates", [])
        rows = candidates if isinstance(candidates, list) else []
        blocked: list[dict[str, Any]] = []
        allowed: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            status = str(row.get("status", "accepted")).lower()
            can_inject = bool(row.get("can_inject", status in {"accepted", "verified"}))
            if status in {"revoked", "superseded", "quarantined", "pending_review"} or not can_inject:
                blocked.append({"id": row.get("id", ""), "reason": "policy_block"})
                continue
            allowed.append(dict(row))
        allowed.sort(
            key=lambda r: (
                float(r.get("confidence", 0.0) or 0.0),
                float(r.get("working_rank", r.get("score", 0.0)) or 0.0),
            ),
            reverse=True,
        )
        return _ok(
            {
                "items": allowed,
                "blocked": blocked,
                "reason_codes": ["open_rank_filtered"],
                "budget": {"low_trust_cap_count": max(1, int(len(allowed) * 0.3))},
            },
            payload,
        )

    async def arank_retrieval(self, payload: Mapping[str, Any]) -> PolicyEnvelope:
        return self.rank_retrieval(payload)

    def run_self_check_specs(self, payload: Mapping[str, Any]) -> PolicyEnvelope:
        check_ids_raw = payload.get("check_ids", [])
        check_ids = [str(x).strip() for x in check_ids_raw] if isinstance(check_ids_raw, list) else []
        allowed = {
            "l4_capture_trend",
            "l4_injection_effectiveness",
            "l4_threshold_suggestions",
            "l4_capacity_projection",
            "l5_llm_notice_state_health",
        }
        results: list[dict[str, Any]] = []
        status = "pass"
        for cid in check_ids:
            if not cid:
                continue
            if cid in allowed:
                results.append({"check_id": cid, "status": "pass", "message": "open_backend_check_pass"})
            else:
                results.append({"check_id": cid, "status": "warn", "message": "open_backend_unknown_check"})
                status = "warn"
        if not results:
            results.append({"check_id": "open_default", "status": "pass", "message": "open_backend_noop_check"})
        return _ok({"status": status, "results": results, "check_ids": check_ids}, payload)

    async def arun_self_check_specs(self, payload: Mapping[str, Any]) -> PolicyEnvelope:
        return self.run_self_check_specs(payload)

    def plan_self_repair(self, payload: Mapping[str, Any]) -> PolicyEnvelope:
        plan_raw = payload.get("plan", [])
        mode = str(payload.get("mode", "safe")).lower().strip()
        rows = [row for row in plan_raw if isinstance(row, dict)] if isinstance(plan_raw, list) else []
        actions: list[dict[str, Any]] = []
        for row in rows:
            risk = str(row.get("risk", "low")).lower()
            action = str(row.get("action", "")).strip()
            if not action:
                continue
            if mode in {"safe", "dry-run"} and risk in {"critical", "high"}:
                actions.append({**row, "decision": "manual_required", "reason": "risk_high_in_safe_mode"})
            else:
                actions.append({**row, "decision": "allow", "reason": "open_backend_repair_allow"})
        status = "ok" if actions else "noop"
        return _ok({"actions": actions, "status": status, "mode": mode}, payload)

    async def aplan_self_repair(self, payload: Mapping[str, Any]) -> PolicyEnvelope:
        return self.plan_self_repair(payload)

    def shadow_decide(self, payload: Mapping[str, Any]) -> PolicyEnvelope:
        kind = str(payload.get("kind", "")).strip().lower()
        if kind == "write_takeover":
            sealed = bool(payload.get("sealed", False))
            level = str(payload.get("seal_level", "soft")).lower()
            risk = str(payload.get("risk", "high")).lower()
            takeover = sealed and (level == "hard" or risk in {"high", "critical"})
            mode = "takeover" if takeover else "observe"
            return _ok({"takeover": takeover, "allow": True, "mode": mode}, payload)
        if kind == "recovery_admission":
            text = str(payload.get("text", "")).strip()
            route = "rejected" if self._looks_like_noise(text) else "accepted"
            return _ok({"route": route, "allow": route == "accepted", "mode": "observe"}, payload)
        return _ok({"allow": True, "mode": "observe"}, payload)

    async def ashadow_decide(self, payload: Mapping[str, Any]) -> PolicyEnvelope:
        return self.shadow_decide(payload)

    def classify_intent(self, payload: Mapping[str, Any]) -> PolicyEnvelope:
        text = str(payload.get("text", "")).strip().lower()
        intent = "statement"
        if any(k in text for k in ["why", "为什么", "本质", "机制"]):
            intent = "explore"
        elif any(k in text for k in ["risk", "安全", "漏洞", "失控"]):
            intent = "risk"
        elif any(k in text for k in ["执行", "步骤", "命令", "实现"]):
            intent = "execute"
        return _ok({"intent": intent}, payload)

    def identify_topic(self, payload: Mapping[str, Any]) -> PolicyEnvelope:
        text = str(payload.get("text", "")).strip().lower()
        topic = "general"
        if "shadow" in text or "影子" in text:
            topic = "shadow_system"
        elif "mcp" in text or "connect" in text:
            topic = "connectivity"
        elif "memory" in text or "记忆" in text:
            topic = "memory_runtime"
        return _ok({"topic": topic}, payload)

    @staticmethod
    def _looks_like_noise(text: str) -> bool:
        raw = str(text or "").strip()
        if len(raw) <= 2:
            return True
        if raw.lower() in {"ok", "好的", "收到", "继续", "yes", "no", "ty"}:
            return True
        return bool(re.fullmatch(r"[\\W_]+", raw))

    @staticmethod
    def _redact_sensitive(text: str) -> tuple[str, list[str]]:
        out = text
        flags: list[str] = []
        token_pattern = re.compile(r"(?i)(api[_-]?key|token|secret)\\s*[:=]\\s*[A-Za-z0-9_\\-]{8,}")
        if token_pattern.search(out):
            out = token_pattern.sub("[REDACTED_CREDENTIAL]", out)
            flags.append("credential_high_risk")
        email_pattern = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}")
        if email_pattern.search(out):
            out = email_pattern.sub("[REDACTED_EMAIL]", out)
            flags.append("email")
        return out, flags

    @staticmethod
    def _looks_like_conflict(text: str) -> bool:
        raw = str(text or "")
        return ("启用" in raw and "禁用" in raw) or ("enable" in raw.lower() and "disable" in raw.lower())
