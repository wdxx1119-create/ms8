from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AdmissionDecision:
    normalized_text: str
    route: str = "accepted"
    reasons: list[str] = field(default_factory=list)
    privacy_flags: list[str] = field(default_factory=list)
    conflict_flags: list[str] = field(default_factory=list)
    risk_scores: dict[str, float] = field(default_factory=dict)
    should_persist_main: bool = True
    should_index: bool = True
    should_write_memory_md: bool = True
    redacted: bool = False
    replace_old: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
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


def evaluate_candidate(text: str, metadata: dict[str, Any] | None = None) -> AdmissionDecision:
    normalized = " ".join(str(text or "").strip().split())
    return AdmissionDecision(
        normalized_text=normalized,
        reasons=["engine_core_admission_compat"],
        raw={"metadata": metadata or {}},
    )


def redact_sensitive_text(text: str) -> dict[str, str]:
    s = str(text or "")
    s = re.sub(r"(?i)(api[_-]?key|token|password)\s*[:=]\s*([^\s,;]+)", r"\1=[REDACTED]", s)
    s = re.sub(r"\b\d{16,}\b", "[REDACTED_NUM]", s)
    return {"redacted_text": s}


class BatchReview:
    def __init__(self, review_service: Any) -> None:
        self.review_service = review_service

    def run(
        self,
        mode: str = "accept_all",
        limit: int | None = None,
        accept_conf_min: float = 0.62,
        reject_conf_max: float = 0.20,
        per_category_limit: int | None = None,
        drain_reject_conf_max: float = 0.50,
    ) -> dict[str, Any]:
        return {
            "status": "success",
            "mode": mode,
            "limit": limit,
            "accept_conf_min": accept_conf_min,
            "reject_conf_max": reject_conf_max,
            "per_category_limit": per_category_limit,
            "drain_reject_conf_max": drain_reject_conf_max,
            "applied": 0,
        }
