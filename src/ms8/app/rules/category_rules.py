from __future__ import annotations

from ms8.app.rules.base import BaseRule, build_meta


def build_category_rules() -> list[BaseRule]:
    raws = [
        {
            "rule_id": "cat.work_report",
            "priority": 10,
            "confidence": 0.76,
            "patterns": [r"完成|已完成|进度|本周|日报|周报"],
            "tags": ["work"],
        },
        {
            "rule_id": "cat.plan",
            "priority": 11,
            "confidence": 0.72,
            "patterns": [r"计划|下一步|待办|todo|roadmap"],
            "tags": ["planning"],
        },
        {
            "rule_id": "cat.decision",
            "priority": 12,
            "confidence": 0.78,
            "patterns": [r"决定|最终|采用|选择|定为"],
            "tags": ["decision"],
        },
        {
            "rule_id": "cat.configuration",
            "priority": 13,
            "confidence": 0.74,
            "patterns": [r"配置|参数|env|yaml|json|设置"],
            "tags": ["config"],
        },
        {
            "rule_id": "cat.technical_doc",
            "priority": 14,
            "confidence": 0.73,
            "patterns": [r"架构|设计|接口|API|模块|实现"],
            "tags": ["technical"],
        },
        {
            "rule_id": "cat.test_result",
            "priority": 15,
            "confidence": 0.75,
            "patterns": [r"测试|通过|失败|coverage|断言|回归"],
            "tags": ["test"],
        },
        {
            "rule_id": "cat.preference",
            "priority": 16,
            "confidence": 0.71,
            "patterns": [r"偏好|喜欢|不喜欢|希望|习惯|建议"],
            "tags": ["preference"],
        },
    ]
    return [BaseRule(build_meta(r)) for r in raws]
