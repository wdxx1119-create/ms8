from __future__ import annotations

from typing import Dict

from app.classifier.llm_classifier import LLMClassifier
from app.classifier.rule_classifier import RuleClassifier
from app.classifier.threshold_manager import ThresholdManager


class HybridClassifier:
    def __init__(self, rule_classifier: RuleClassifier, llm_classifier: LLMClassifier, threshold_manager: ThresholdManager) -> None:
        self.rule_classifier = rule_classifier
        self.llm_classifier = llm_classifier
        self.threshold_manager = threshold_manager

    def classify(self, text: str, context: Dict) -> Dict:
        category, confidence, tags, matched_rules = self.rule_classifier.classify(text)
        gray_reason = None

        if not category:
            gray_reason = "no_rule_match"
        elif not self.threshold_manager.pass_threshold(category, confidence):
            gray_reason = "low_rule_confidence"
        elif context.get("has_code") and category in {"technical_doc", "configuration"} and confidence < 0.8:
            gray_reason = "context_dependency"
        elif len(context.get("recent_categories", [])) >= 2 and category not in context.get("recent_categories", []):
            gray_reason = "mixed_intent"

        if not gray_reason:
            return {
                "category": category,
                "confidence": confidence,
                "tags": tags,
                "matched_rules": matched_rules,
                "llm_used": False,
                "reason": "rule_high_confidence",
            }

        ok, llm_result, err = self.llm_classifier.classify(text, context)
        if not ok:
            return {
                "category": category or "technical_doc",
                "confidence": confidence if category else 0.5,
                "tags": tags,
                "matched_rules": matched_rules,
                "llm_used": False,
                "reason": f"rule_fallback:{gray_reason}",
                "llm_error": err,
            }

        out_tags = sorted(set(tags + llm_result.get("tags", [])))
        return {
            "category": llm_result["category"],
            "confidence": llm_result["confidence"],
            "tags": out_tags,
            "matched_rules": matched_rules,
            "llm_used": True,
            "reason": llm_result.get("reason", gray_reason),
            "gray_reason": gray_reason,
        }
