from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.pipeline.risk_scoring import compute_risk_scores
from app.rules.block_rules import evaluate_block
from app.rules.conflict_rules import evaluate_conflict
from app.rules.privacy_rules import redact_sensitive_text


@dataclass
class AdmissionDecision:
    normalized_text: str
    route: str
    reasons: List[str] = field(default_factory=list)
    privacy_flags: List[str] = field(default_factory=list)
    conflict_flags: List[str] = field(default_factory=list)
    risk_scores: Dict[str, float] = field(default_factory=dict)
    should_persist_main: bool = False
    should_index: bool = False
    should_write_memory_md: bool = False
    redacted: bool = False
    replace_old: bool = False
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "normalized_text": self.normalized_text,
            "route": self.route,
            "reasons": self.reasons,
            "privacy_flags": self.privacy_flags,
            "conflict_flags": self.conflict_flags,
            "risk_scores": self.risk_scores,
            "should_persist_main": self.should_persist_main,
            "should_index": self.should_index,
            "should_write_memory_md": self.should_write_memory_md,
            "redacted": self.redacted,
            "replace_old": self.replace_old,
            "raw": self.raw,
        }


def _worthiness_score(text: str) -> float:
    payload = str(text or "").strip()
    if not payload:
        return 0.0
    score = 0.25
    if len(payload) >= 8:
        score += 0.2
    if any(k in payload for k in ["决定", "计划", "配置", "阈值", "启用", "禁用", "版本"]):
        score += 0.4
    if any(k in payload.lower() for k in ["enable", "disable", "decision", "plan", "config"]):
        score += 0.2
    return min(1.0, score)


def evaluate_candidate(
    text: str,
    metadata: Optional[dict] = None,
    candidate_type: Optional[str] = None,
    existing_memories: Optional[list] = None,
) -> AdmissionDecision:
    _ = metadata, candidate_type, existing_memories
    normalized = str(text or "").strip()
    block_result = evaluate_block(normalized)
    privacy_result = redact_sensitive_text(normalized)
    conflict_result = evaluate_conflict(normalized)
    worthiness = _worthiness_score(normalized)
    risk_scores = compute_risk_scores(normalized, block_result, privacy_result, conflict_result)

    route = "accepted"
    reasons: List[str] = []
    should_persist_main = True
    should_index = True
    should_write_memory_md = True
    redacted = False
    replace_old = False
    final_text = normalized

    if bool(block_result.get("blocked", False)):
        suggested = str(block_result.get("suggested_route", "rejected"))
        route = suggested if suggested in {"rejected", "short_term_only"} else "rejected"
        reasons.append(str(block_result.get("reason", "blocked")))
        should_persist_main = False
        should_index = False
        should_write_memory_md = False
    elif worthiness < 0.35:
        route = "short_term_only"
        reasons.append("low_worthiness")
        should_persist_main = False
        should_index = False
        should_write_memory_md = False

    if bool(privacy_result.get("has_sensitive", False)):
        flags = list(privacy_result.get("flags", []))
        reasons.append("privacy_hit")
        if any(x in flags for x in {"ssh_private_key", "password_field"}):
            route = "pending_review"
            should_persist_main = True
            should_index = False
            should_write_memory_md = False
        else:
            route = "redacted_accept" if route != "rejected" else route
            final_text = str(privacy_result.get("redacted_text", normalized))
            redacted = True
            should_persist_main = route != "rejected"
            should_index = route == "redacted_accept"
            should_write_memory_md = route == "redacted_accept"

    if bool(conflict_result.get("has_conflict", False)):
        conflict_resolution = str(conflict_result.get("resolution", "pending_review"))
        reasons.append(str(conflict_result.get("reason", "conflict")))
        if conflict_resolution == "replace_old":
            replace_old = True
            if route not in {"rejected", "short_term_only"}:
                route = "accepted"
        elif conflict_resolution == "pending_review":
            if route not in {"rejected", "short_term_only"}:
                route = "pending_review"
                should_index = False
                should_write_memory_md = False

    if not reasons:
        reasons.append("admission_default_accept")

    return AdmissionDecision(
        normalized_text=final_text,
        route=route,
        reasons=reasons,
        privacy_flags=list(privacy_result.get("flags", [])),
        conflict_flags=list(conflict_result.get("conflict_flags", [])),
        risk_scores=risk_scores,
        should_persist_main=should_persist_main,
        should_index=should_index,
        should_write_memory_md=should_write_memory_md,
        redacted=redacted,
        replace_old=replace_old,
        raw={
            "block": block_result,
            "privacy": privacy_result,
            "conflict": conflict_result,
            "worthiness_score": worthiness,
        },
    )
