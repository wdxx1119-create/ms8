"""Expression preference profile lifecycle helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, cast

from .response_mode_types import ConversationState, ExpressionPreferenceProfile, RouterDecision

_PROFILE_FILE = "expression_preference_profile.json"
_STATE_FILE = "expression_router_state.json"
DEFAULT_PROFILE_DECAY = 0.95


def clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(v)))


def _profile_from_dict(obj: dict[str, Any] | None) -> ExpressionPreferenceProfile:
    data = obj if isinstance(obj, dict) else {}
    return ExpressionPreferenceProfile(
        abstract_score=clamp(float(data.get("abstract_score", 0.5) or 0.5)),
        concrete_score=clamp(float(data.get("concrete_score", 0.5) or 0.5)),
        divergent_score=clamp(float(data.get("divergent_score", 0.5) or 0.5)),
        convergent_score=clamp(float(data.get("convergent_score", 0.5) or 0.5)),
        logic_score=clamp(float(data.get("logic_score", 0.5) or 0.5)),
        action_score=clamp(float(data.get("action_score", 0.5) or 0.5)),
        evidence_count=int(data.get("evidence_count", 0) or 0),
        last_updated_round=int(data.get("last_updated_round", 0) or 0),
    )


def _state_from_dict(obj: dict[str, Any] | None) -> ConversationState:
    data = obj if isinstance(obj, dict) else {}
    raw_last_mode = data.get("last_mode")
    if raw_last_mode in {"normal", "light", "strong"}:
        last_mode: Literal["normal", "light", "strong"] | None = cast(
            Literal["normal", "light", "strong"], raw_last_mode
        )
    else:
        last_mode = None
    return ConversationState(
        last_mode=last_mode,
        strong_count=int(data.get("strong_count", 0) or 0),
        rounds_since_strong_signal=int(data.get("rounds_since_strong_signal", 0) or 0),
        current_round=int(data.get("current_round", 0) or 0),
        last_cognitive_phrase=str(data.get("last_cognitive_phrase", "") or "") or None,
    )


def load_expression_profile(memory_dir: Path) -> ExpressionPreferenceProfile:
    p = Path(memory_dir) / _PROFILE_FILE
    if not p.exists():
        return ExpressionPreferenceProfile()
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ExpressionPreferenceProfile()
    return _profile_from_dict(obj if isinstance(obj, dict) else {})


def save_expression_profile(memory_dir: Path, profile: ExpressionPreferenceProfile) -> None:
    p = Path(memory_dir) / _PROFILE_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(profile.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def load_conversation_state(memory_dir: Path) -> ConversationState:
    p = Path(memory_dir) / _STATE_FILE
    if not p.exists():
        return ConversationState()
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ConversationState()
    return _state_from_dict(obj if isinstance(obj, dict) else {})


def save_conversation_state(memory_dir: Path, state: ConversationState) -> None:
    p = Path(memory_dir) / _STATE_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_expression_profile_dir(memory_dir: Path | None = None) -> Path:
    if memory_dir is None:
        try:
            from ms8.runtime import get_runtime_dir

            return get_runtime_dir() / "memory"
        except (ImportError, OSError):
            return Path.cwd() / ".ms8" / "memory"
    return Path(memory_dir)


def prepare_profile_for_round(
    profile: ExpressionPreferenceProfile,
    current_round: int,
    *,
    decay: float = DEFAULT_PROFILE_DECAY,
) -> tuple[ExpressionPreferenceProfile, bool]:
    """Decay profile and reset stale profile. Returns (profile, is_valid_for_use)."""
    p = _profile_from_dict(profile.to_dict())
    d = clamp(float(decay), 0.5, 1.0)
    p.abstract_score = clamp(p.abstract_score * d)
    p.concrete_score = clamp(p.concrete_score * d)
    p.divergent_score = clamp(p.divergent_score * d)
    p.convergent_score = clamp(p.convergent_score * d)
    p.logic_score = clamp(p.logic_score * d)
    p.action_score = clamp(p.action_score * d)

    if int(current_round) - int(p.last_updated_round) > 20:
        p = ExpressionPreferenceProfile(last_updated_round=int(current_round))
        return p, False

    is_valid = p.evidence_count >= 3 and (int(current_round) - int(p.last_updated_round) <= 20)
    return p, is_valid


def update_profile_from_decision(
    profile: ExpressionPreferenceProfile,
    decision: RouterDecision,
    current_round: int,
) -> ExpressionPreferenceProfile:
    p = _profile_from_dict(profile.to_dict())

    if decision.mode not in {"light", "strong"}:
        return p
    if not decision.signal_categories:
        return p

    delta_map = {
        "explore": {"abstract_score": 0.25, "divergent_score": 0.10},
        "execute": {"action_score": 0.25, "concrete_score": 0.15},
        "stuck": {"logic_score": 0.20, "concrete_score": 0.10},
        "risk": {"logic_score": 0.25, "convergent_score": 0.15},
        "decision": {"convergent_score": 0.25, "logic_score": 0.15},
    }
    applied = False
    for category in set(decision.signal_categories):
        for key, delta in delta_map.get(category, {}).items():
            val = float(getattr(p, key))
            setattr(p, key, clamp(val * 0.95 + delta))
            applied = True
    if applied:
        p.evidence_count += 1
        p.last_updated_round = int(current_round)
    return p


def update_conversation_state(state: ConversationState, decision: RouterDecision) -> ConversationState:
    return update_conversation_state_with_policy(state, decision, reset_rounds_without_strong=3)


def update_conversation_state_with_policy(
    state: ConversationState,
    decision: RouterDecision,
    *,
    reset_rounds_without_strong: int = 3,
) -> ConversationState:
    s = _state_from_dict(state.to_dict())
    s.current_round += 1
    s.last_mode = decision.mode
    if decision.mode == "strong":
        s.strong_count += 1
        s.rounds_since_strong_signal = 0
    else:
        s.rounds_since_strong_signal += 1
        if s.rounds_since_strong_signal >= max(1, int(reset_rounds_without_strong)):
            s.strong_count = 0
    return s
