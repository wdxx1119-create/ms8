"""Lightweight expression adaptation router."""

from __future__ import annotations

import re

from .response_mode_types import ConversationState, ExpressionPreferenceProfile, RouterDecision

DEFAULT_NEGATIONS = ("不是", "并非", "没有", "别", "不用", "无需", "不需要", "不要", "无关", "不算")
DEFAULT_SENTENCE_SPLIT_REGEX = r"[，。！？；,.!?;]+"

DEFAULT_SIGNALS: dict[str, tuple[float, str]] = {
    "本质": (1.0, "explore"),
    "机制": (1.0, "explore"),
    "原理": (1.0, "explore"),
    "为什么": (1.0, "explore"),
    "怎么设计": (1.0, "explore"),
    "更高维": (1.0, "explore"),
    "思路": (1.0, "explore"),
    "方向": (1.0, "explore"),
    "直接给": (0.5, "execute"),
    "执行版": (0.5, "execute"),
    "任务书": (0.5, "execute"),
    "步骤": (0.5, "execute"),
    "命令": (0.5, "execute"),
    "落地": (0.5, "execute"),
    "实现": (0.5, "execute"),
    "codex执行": (0.5, "execute"),
    "代码": (0.5, "execute"),
    "卡住": (1.5, "stuck"),
    "不对": (1.5, "stuck"),
    "乱": (1.5, "stuck"),
    "矛盾": (1.5, "stuck"),
    "推不动": (1.5, "stuck"),
    "哪里有问题": (1.5, "stuck"),
    "隐患": (1.5, "stuck"),
    "风险": (2.0, "risk"),
    "漏洞": (2.0, "risk"),
    "攻击": (2.0, "risk"),
    "兜底": (2.0, "risk"),
    "安全": (2.0, "risk"),
    "失控": (2.0, "risk"),
    "边界": (2.0, "risk"),
    "选哪个": (1.0, "decision"),
    "优先级": (1.0, "decision"),
    "取舍": (1.0, "decision"),
    "哪个更好": (1.0, "decision"),
    "推荐": (1.0, "decision"),
    "二选一": (1.0, "decision"),
}

DEFAULT_FORCE_NORMAL_WORDS = ("直接", "只给", "仅需", "不要解释", "别解释", "命令", "代码块", "执行版", "任务书")


def _as_float(value: object, default: float) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return float(default)
    return float(default)


def _as_int(value: object, default: int) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return int(default)
    return int(default)


def _resolve_router_config(router_config: dict | None) -> dict:
    cfg = router_config if isinstance(router_config, dict) else {}
    thresholds = cfg.get("thresholds", {}) if isinstance(cfg.get("thresholds"), dict) else {}
    cooldown = cfg.get("cooldown", {}) if isinstance(cfg.get("cooldown"), dict) else {}
    negation = cfg.get("negation", {}) if isinstance(cfg.get("negation"), dict) else {}
    signals_cfg = cfg.get("signals", {}) if isinstance(cfg.get("signals"), dict) else {}
    force_cfg = cfg.get("force_normal", {}) if isinstance(cfg.get("force_normal"), dict) else {}

    signals: dict[str, tuple[float, str]] = {}
    default_weights = {
        "explore": 1.0,
        "execute": 0.5,
        "stuck": 1.5,
        "risk": 2.0,
        "decision": 1.0,
    }
    for category in ("explore", "execute", "stuck", "risk", "decision"):
        category_cfg = signals_cfg.get(category, {}) if isinstance(signals_cfg.get(category), dict) else {}
        weight = _as_float(
            category_cfg.get("weight", default_weights[category]),
            default_weights[category],
        )
        keywords = category_cfg.get("keywords")
        if not isinstance(keywords, list) or not keywords:
            keywords = [k for k, (_, c) in DEFAULT_SIGNALS.items() if c == category]
        for keyword in keywords:
            token = str(keyword or "").strip().lower()
            if token:
                signals[token] = (weight, category)

    neg_words = negation.get("words")
    if not isinstance(neg_words, list) or not neg_words:
        neg_words = list(DEFAULT_NEGATIONS)

    force_words = force_cfg.get("keywords")
    if not isinstance(force_words, list) or not force_words:
        force_words = list(DEFAULT_FORCE_NORMAL_WORDS)

    return {
        "strong_min_weight": _as_float(thresholds.get("strong_min_weight", 2.0), 2.0),
        "light_min_weight": _as_float(thresholds.get("light_min_weight", 1.0), 1.0),
        "execute_only_normal_max_weight": _as_float(thresholds.get("execute_only_normal_max_weight", 0.5), 0.5),
        "confidence_divisor": max(0.1, _as_float(thresholds.get("confidence_divisor", 3.0), 3.0)),
        "cooldown_enabled": bool(cooldown.get("enabled", True)),
        "reset_rounds_without_strong": _as_int(cooldown.get("reset_rounds_without_strong", 3), 3),
        "max_continuous_strong": _as_int(cooldown.get("max_continuous_strong", 2), 2),
        "confidence_penalty": _as_float(cooldown.get("confidence_penalty", 0.2), 0.2),
        "confidence_floor": _as_float(cooldown.get("confidence_floor", 0.5), 0.5),
        "negation_window_chars": max(0, _as_int(negation.get("window_chars", 8), 8)),
        "sentence_split_regex": str(negation.get("sentence_split_regex", DEFAULT_SENTENCE_SPLIT_REGEX)),
        "negation_words": tuple(str(w or "").strip() for w in neg_words if str(w or "").strip()),
        "signals": signals or DEFAULT_SIGNALS,
        "force_normal_words": tuple(str(w or "").strip().lower() for w in force_words if str(w or "").strip()),
    }


def _is_code_only_like(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    if raw.startswith("```") and raw.endswith("```"):
        return True
    # simple heuristic: many code symbols and very few CJK chars.
    symbol_count = len(re.findall(r"[{}();=<>\[\]`$#/:*+-]", raw))
    cjk_count = len(re.findall(r"[\u4e00-\u9fff]", raw))
    return symbol_count >= 8 and cjk_count <= 2


def _contains_negation_before_keyword(
    sentence: str,
    keyword: str,
    *,
    negation_words: tuple[str, ...],
    window_chars: int,
) -> bool:
    idx = sentence.find(keyword)
    while idx >= 0:
        start = max(0, idx - window_chars)
        prefix = sentence[start:idx]
        if any(n in prefix for n in negation_words):
            return True
        idx = sentence.find(keyword, idx + len(keyword))
    return False


def _collect_signals(
    text: str,
    *,
    signals: dict[str, tuple[float, str]],
    sentence_split_regex: str,
    negation_words: tuple[str, ...],
    negation_window_chars: int,
) -> tuple[list[str], list[str], float]:
    matched: list[str] = []
    categories: list[str] = []
    total = 0.0
    sentence_split = re.compile(sentence_split_regex)
    sentences = [s.strip() for s in sentence_split.split(text) if s.strip()]
    for keyword, (weight, category) in signals.items():
        hit = False
        for sentence in sentences:
            if keyword not in sentence:
                continue
            if _contains_negation_before_keyword(
                sentence,
                keyword,
                negation_words=negation_words,
                window_chars=negation_window_chars,
            ):
                continue
            hit = True
            break
        if hit:
            matched.append(keyword)
            categories.append(category)
            total += float(weight)
    return matched, categories, total


def _profile_adjustments(profile: ExpressionPreferenceProfile) -> list[str]:
    adjustments: list[str] = []
    if profile.abstract_score > 0.7:
        adjustments.append("abstract_hint")
    if profile.concrete_score > 0.7:
        adjustments.append("concrete_hint")
    if profile.divergent_score > 0.7:
        adjustments.append("divergent_hint")
    if profile.convergent_score > 0.7:
        adjustments.append("convergent_hint")
    if profile.logic_score > 0.7:
        adjustments.append("logic_hint")
    if profile.action_score > 0.6:
        adjustments.append("action_hint")
    return adjustments


def choose_cognitive_phrase(mode: str, last_phrase: str | None = None) -> str | None:
    if mode not in {"light", "strong"}:
        return None
    phrases = ["你会发现", "其实更像是", "很多时候不是……而是……"]
    for phrase in phrases:
        if phrase != (last_phrase or "").strip():
            return phrase
    return None


def route_response(
    user_message: str,
    recent_summary: str = "",
    profile: ExpressionPreferenceProfile | None = None,
    conversation_state: ConversationState | None = None,
    router_config: dict | None = None,
) -> RouterDecision:
    cfg = _resolve_router_config(router_config)
    user_text = str(user_message or "").strip()
    summary_text = str(recent_summary or "").strip()
    text = f"{user_text} {summary_text}".strip().lower()
    if not text:
        return RouterDecision(mode="normal", confidence=1.0, reason="empty_input_normal")
    if _is_code_only_like(user_text):
        return RouterDecision(mode="normal", confidence=1.0, reason="code_only_input_normal")
    matched, categories, total_weight = _collect_signals(
        text,
        signals=cfg["signals"],
        sentence_split_regex=cfg["sentence_split_regex"],
        negation_words=cfg["negation_words"],
        negation_window_chars=cfg["negation_window_chars"],
    )
    category_set = set(categories)
    execute_signals = {k for k, v in cfg["signals"].items() if v[1] == "execute"}

    # Base mode by threshold.
    if total_weight >= cfg["strong_min_weight"]:
        mode = "strong"
        reason = "threshold_strong"
    elif total_weight >= cfg["light_min_weight"]:
        mode = "light"
        reason = "threshold_light"
    else:
        mode = "normal"
        reason = "threshold_normal"

    # Execution-only weak intent should stay normal.
    execute_only = bool(category_set) and category_set.issubset({"execute"})
    if execute_only and total_weight <= cfg["execute_only_normal_max_weight"]:
        mode = "normal"
        reason = "execute_only_normal"

    # Forced normal when user asks purely execution output.
    if any(k in text for k in execute_signals) and any(w in text for w in cfg["force_normal_words"]):
        mode = "normal"
        reason = "force_normal_execution_output"

    # Explicit negation against "code" should not trigger execute routing.
    if re.search(r"(不需要|无需|不要|别)\s*代码", text):
        # Only force normal when execution-style intent dominates.
        if category_set.issubset({"execute"}) or not category_set:
            mode = "normal"
            reason = "negated_code_request_normal"

    original_mode = mode
    cooldown_applied = False
    risk_stuck_dual = "risk" in category_set and "stuck" in category_set

    state = conversation_state or ConversationState()
    # Cooldown rule: after strong, next non-risk+stuck turn should be softened.
    if cfg["cooldown_enabled"] and state.last_mode == "strong" and not risk_stuck_dual:
        cooldown_applied = True
        if mode == "strong":
            mode = "light"
            reason = "cooldown_after_strong"
        elif mode == "light":
            mode = "normal"
            reason = "cooldown_after_strong"
        else:
            reason = "cooldown_checked_keep_normal"

    if (
        cfg["cooldown_enabled"]
        and mode == "strong"
        and state.strong_count >= cfg["max_continuous_strong"]
        and not risk_stuck_dual
    ):
        cooldown_applied = True
        mode = "normal"
        reason = "continuous_strong_cooldown"

    if mode == "normal" and total_weight == 0:
        confidence = 1.0
    else:
        confidence = min(1.0, float(total_weight) / cfg["confidence_divisor"])
    if cooldown_applied and mode != original_mode:
        confidence = max(cfg["confidence_floor"], confidence - cfg["confidence_penalty"])

    profile_used = False
    adjustments: list[str] = []
    if isinstance(profile, ExpressionPreferenceProfile):
        # caller should provide already-validated profile. here we only trust clear valid profile.
        if profile.evidence_count >= 3 and (state.current_round - profile.last_updated_round) <= 20:
            profile_used = True
            adjustments = _profile_adjustments(profile)

    if mode in {"light", "strong"} and state.last_cognitive_phrase:
        next_phrase = choose_cognitive_phrase(mode, state.last_cognitive_phrase)
        if next_phrase and next_phrase != state.last_cognitive_phrase:
            reason = f"{reason}|avoid_repeat:{state.last_cognitive_phrase}->{next_phrase}"
        else:
            reason = f"{reason}|avoid_repeat:{state.last_cognitive_phrase}"

    return RouterDecision(
        mode=mode,  # type: ignore[arg-type]
        confidence=confidence,
        matched_signals=matched,
        signal_categories=sorted(set(categories)),
        total_weight=total_weight,
        cooldown_applied=cooldown_applied,
        profile_used=profile_used,
        profile_adjustments=adjustments,
        reason=reason,
    )
