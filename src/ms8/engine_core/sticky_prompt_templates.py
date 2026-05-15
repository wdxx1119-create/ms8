"""Prompt extras for expression adaptation."""

from __future__ import annotations

from .response_mode_types import ExpressionPreferenceProfile, ResponseMode

LIGHT_PROMPT_EXTRA = """
当前触发 light 表达增强。

保持自然回答，不要模板化。
你可以选用以下低侵入认知转向句式，最多一句，不要连用：
- 你会发现……
- 其实更像是……
- 很多时候不是……而是……

尽量加入一句可复述锚点句，例如：
- 关键不是 A，而是 B。
- 问题不在表面，在结构。

要求：
- 不强制情景化开头。
- 不强制行动结尾。
- 不判断用户人格。
- 每轮最多使用一个典型认知转向句式。
- 避免与上一轮使用相同句式。
- 如果无法自然插入，则不要硬插。
- 自然表达优先于模板完整。
""".strip()

STRONG_PROMPT_EXTRA = """
当前触发 strong 表达增强。

请使用更有触感的组织方式，但避免套路感。

输出要求：
1. 用一句情景化表达或直接判断开头。
2. 做低侵入认知转向，最多使用一个典型句式。
3. 将核心内容压缩成 2–4 个简短要点。
4. 必须有一句可单独记住的锚点句。
5. 结尾给一个小动作或自然延续。
6. 不判断用户人格，不贴标签。
7. 避免连续使用相同表达模板。
8. 锚点句不要每次都使用“关键不是 A，而是 B”，允许自然变体。
9. 如果某个模板句不自然，优先自然表达。
""".strip()

GUARDRAIL_PROMPT_EXTRA = """
全局安全边界：
- 不输出任何人格或类型标签判断。
- 不替用户做最终决策，优先给判断方法和边界。
- 不把表达偏好当作事实。
- 仅做表达微调，不改变事实结论与逻辑结论。
""".strip()


def get_prompt_extra(mode: ResponseMode) -> str:
    if mode == "light":
        return LIGHT_PROMPT_EXTRA
    if mode == "strong":
        return STRONG_PROMPT_EXTRA
    return ""


def build_profile_hint(profile: ExpressionPreferenceProfile) -> str:
    lines: list[str] = [
        "当前表达偏好提示（仅微调用，不改变事实判断）：",
    ]
    if profile.abstract_score > 0.7:
        lines.append("- 可适当强调结构、机制、原则，但不要过度抽象。")
    if profile.concrete_score > 0.7:
        lines.append("- 优先使用具体例子、步骤、操作。")
    if profile.divergent_score > 0.7:
        lines.append("- 可给 2–3 个可能路径，不急于收死。")
    if profile.convergent_score > 0.7:
        lines.append("- 先给方向，再解释理由。")
    if profile.logic_score > 0.7:
        lines.append("- 强化因果链和结构。")
    if profile.action_score > 0.6:
        lines.append("- 结尾可更快给出小动作或验证步骤。")
    lines.append("")
    lines.append("注意：")
    lines.append("- 不要根据偏好改变事实结论。")
    lines.append("- 不输出任何人格或类型标签。")
    return "\n".join(lines).strip()

