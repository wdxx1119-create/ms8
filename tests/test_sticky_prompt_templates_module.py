from __future__ import annotations

from ms8.engine_core.response_mode_types import ExpressionPreferenceProfile
from ms8.engine_core.sticky_prompt_templates import (
    GUARDRAIL_PROMPT_EXTRA,
    LIGHT_PROMPT_EXTRA,
    STRONG_PROMPT_EXTRA,
    build_profile_hint,
    get_prompt_extra,
)


def test_get_prompt_extra_modes() -> None:
    assert get_prompt_extra("normal") == ""
    assert get_prompt_extra("light") == LIGHT_PROMPT_EXTRA
    assert get_prompt_extra("strong") == STRONG_PROMPT_EXTRA


def test_build_profile_hint_selective_lines() -> None:
    profile = ExpressionPreferenceProfile(
        abstract_score=0.8,
        concrete_score=0.5,
        divergent_score=0.75,
        convergent_score=0.2,
        logic_score=0.71,
        action_score=0.61,
        evidence_count=5,
        last_updated_round=10,
    )
    text = build_profile_hint(profile)
    assert "结构、机制、原则" in text
    assert "2–3 个可能路径" in text
    assert "强化因果链和结构" in text
    assert "小动作或验证步骤" in text
    assert "具体例子、步骤、操作" not in text
    assert "先给方向，再解释理由" not in text


def test_build_profile_hint_always_has_guard_notes() -> None:
    profile = ExpressionPreferenceProfile(
        abstract_score=0.1,
        concrete_score=0.1,
        divergent_score=0.1,
        convergent_score=0.1,
        logic_score=0.1,
        action_score=0.1,
        evidence_count=0,
        last_updated_round=0,
    )
    text = build_profile_hint(profile)
    assert "当前表达偏好提示" in text
    assert "注意：" in text
    assert "不要根据偏好改变事实结论" in text
    assert "不输出任何人格或类型标签" in text
    assert text.strip() == text


def test_guardrail_prompt_non_empty() -> None:
    assert "全局安全边界" in GUARDRAIL_PROMPT_EXTRA
    assert "不输出任何人格或类型标签判断" in GUARDRAIL_PROMPT_EXTRA

