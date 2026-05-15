from __future__ import annotations

from ms8.engine_core.expression_preference_profile import ExpressionPreferenceProfile, prepare_profile_for_round
from ms8.engine_core.response_mode_router import choose_cognitive_phrase, route_response
from ms8.engine_core.response_mode_types import ConversationState
from ms8.engine_core.sticky_prompt_templates import GUARDRAIL_PROMPT_EXTRA, get_prompt_extra


def test_explore_trigger() -> None:
    d = route_response("这个功能的本质是什么？")
    assert d.mode in {"light", "strong"}
    assert "本质" in d.matched_signals


def test_execute_force_normal() -> None:
    d = route_response("直接给我 Codex 执行版任务书，不要解释")
    assert d.mode == "normal"


def test_risk_strong() -> None:
    d = route_response("这里有安全风险和兜底问题")
    assert d.total_weight >= 2.0
    assert d.mode == "strong"
    assert "risk" in d.signal_categories


def test_light_validation_not_strong() -> None:
    d = route_response("这样对吗？")
    assert d.mode in {"normal", "light"}


def test_negation_filter_ignore_keyword() -> None:
    d = route_response("这不是本质问题。")
    assert "本质" not in d.matched_signals
    assert d.mode == "normal"


def test_negation_after_keyword_not_ignored() -> None:
    d = route_response("这个功能的本质不是关键，只要能用。")
    assert "本质" in d.matched_signals
    assert d.mode in {"light", "strong"}


def test_confidence_formula() -> None:
    d = route_response("这里有风险和兜底。")
    assert d.confidence == min(1.0, d.total_weight / 3.0)


def test_cooldown_normal_keep_and_mark() -> None:
    state = ConversationState(last_mode="strong", strong_count=1, rounds_since_strong_signal=0, current_round=2)
    d = route_response("你好", conversation_state=state)
    assert d.mode == "normal"
    assert d.cooldown_applied is True


def test_continuous_strong_cooldown() -> None:
    state = ConversationState(last_mode="strong", strong_count=2, rounds_since_strong_signal=0, current_round=3)
    d = route_response("这个机制有风险", conversation_state=state)
    assert d.mode in {"normal", "light"}
    assert d.cooldown_applied is True


def test_profile_not_used_if_evidence_low() -> None:
    p = ExpressionPreferenceProfile(evidence_count=2, last_updated_round=5)
    s = ConversationState(current_round=6)
    d = route_response("这个机制是什么", profile=p, conversation_state=s)
    assert d.profile_used is False


def test_profile_expired_reset() -> None:
    p = ExpressionPreferenceProfile(
        abstract_score=0.9,
        concrete_score=0.2,
        divergent_score=0.8,
        convergent_score=0.3,
        logic_score=0.7,
        action_score=0.4,
        evidence_count=5,
        last_updated_round=1,
    )
    prepared, valid = prepare_profile_for_round(p, current_round=25)
    assert valid is False
    assert prepared.abstract_score == 0.5
    assert prepared.concrete_score == 0.5
    assert prepared.evidence_count == 0


def test_profile_decay_default_is_stronger() -> None:
    p = ExpressionPreferenceProfile(abstract_score=1.0, concrete_score=1.0, evidence_count=3, last_updated_round=1)
    prepared, _ = prepare_profile_for_round(p, current_round=2)
    assert round(prepared.abstract_score, 2) == 0.95
    assert round(prepared.concrete_score, 2) == 0.95


def test_profile_adjustments() -> None:
    p = ExpressionPreferenceProfile(abstract_score=0.8, evidence_count=5, last_updated_round=9)
    s = ConversationState(current_round=10)
    d = route_response("机制是什么", profile=p, conversation_state=s)
    assert d.profile_used is True
    assert "abstract_hint" in d.profile_adjustments


def test_execute_single_not_strong() -> None:
    d = route_response("给我实现步骤")
    assert d.mode in {"normal", "light"}


def test_prompt_forbidden_labels_not_present() -> None:
    combined = f"{get_prompt_extra('strong')}\n{GUARDRAIL_PROMPT_EXTRA}"
    forbidden = ["MBTI", "荣格八维", "Ni", "Ne", "Ti", "Te", "Fi", "Fe", "Si", "Se", "人格类型"]
    for word in forbidden:
        assert word not in combined


def test_cognitive_phrase_non_enforced_duplicate() -> None:
    # Router does not force phrase selection, but should still route safely.
    state = ConversationState(last_mode="light", last_cognitive_phrase="你会发现", current_round=5)
    d = route_response("这个机制本质是什么", conversation_state=state)
    assert d.mode in {"light", "strong"}


def test_router_config_threshold_override() -> None:
    cfg = {
        "thresholds": {
            "light_min_weight": 1.5,
            "strong_min_weight": 3.0,
        },
    }
    d = route_response("本质", router_config=cfg)
    assert d.mode == "normal"


def test_router_config_missing_uses_defaults() -> None:
    d = route_response("这里有风险", router_config={"thresholds": {"strong_min_weight": "bad"}})
    # bad numeric should not break flow; fallback path should still return a valid mode
    assert d.mode in {"normal", "light", "strong"}


def test_empty_input_normal() -> None:
    d = route_response("   ", recent_summary=" ")
    assert d.mode == "normal"
    assert d.reason == "empty_input_normal"


def test_code_block_input_normal() -> None:
    d = route_response("```python\nfor i in range(3):\n    print(i)\n```")
    assert d.mode == "normal"
    assert d.reason == "code_only_input_normal"


def test_long_input_safe() -> None:
    text = "这是本质问题。" * 300
    d = route_response(text)
    assert d.mode in {"light", "strong"}
    assert d.confidence <= 1.0


def test_all_negation_keywords_should_not_trigger() -> None:
    d = route_response("这不是本质，也不需要代码，无需机制，不要方向。")
    assert d.mode == "normal"


def test_negated_code_should_stay_normal() -> None:
    d = route_response("这里不需要代码，只要结论。")
    assert d.mode == "normal"


def test_negated_code_should_not_override_risk() -> None:
    d = route_response("这里不需要代码，但有严重安全风险和兜底问题。")
    assert d.mode == "strong"


def test_choose_cognitive_phrase_avoids_last() -> None:
    phrase = choose_cognitive_phrase("light", "你会发现")
    assert phrase in {"其实更像是", "很多时候不是……而是……"}
