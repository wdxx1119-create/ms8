"""Types for lightweight expression adaptation routing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ResponseMode = Literal["normal", "light", "strong"]


@dataclass
class RouterDecision:
    mode: ResponseMode = "normal"
    confidence: float = 1.0
    matched_signals: list[str] = field(default_factory=list)
    signal_categories: list[str] = field(default_factory=list)
    total_weight: float = 0.0
    cooldown_applied: bool = False
    profile_used: bool = False
    profile_adjustments: list[str] = field(default_factory=list)
    reason: str = "default_normal"

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "confidence": float(self.confidence),
            "matched_signals": list(self.matched_signals),
            "signal_categories": list(self.signal_categories),
            "total_weight": float(self.total_weight),
            "cooldown_applied": bool(self.cooldown_applied),
            "profile_used": bool(self.profile_used),
            "profile_adjustments": list(self.profile_adjustments),
            "reason": str(self.reason),
        }


@dataclass
class ExpressionPreferenceProfile:
    abstract_score: float = 0.5
    concrete_score: float = 0.5
    divergent_score: float = 0.5
    convergent_score: float = 0.5
    logic_score: float = 0.5
    action_score: float = 0.5
    evidence_count: int = 0
    last_updated_round: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "abstract_score": float(self.abstract_score),
            "concrete_score": float(self.concrete_score),
            "divergent_score": float(self.divergent_score),
            "convergent_score": float(self.convergent_score),
            "logic_score": float(self.logic_score),
            "action_score": float(self.action_score),
            "evidence_count": int(self.evidence_count),
            "last_updated_round": int(self.last_updated_round),
        }


@dataclass
class ConversationState:
    last_mode: ResponseMode | None = None
    strong_count: int = 0
    rounds_since_strong_signal: int = 0
    current_round: int = 0
    last_cognitive_phrase: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "last_mode": self.last_mode,
            "strong_count": int(self.strong_count),
            "rounds_since_strong_signal": int(self.rounds_since_strong_signal),
            "current_round": int(self.current_round),
            "last_cognitive_phrase": self.last_cognitive_phrase,
        }

