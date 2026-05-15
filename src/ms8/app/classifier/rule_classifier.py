from __future__ import annotations

from ms8.app.rules.base import BaseRule


class RuleClassifier:
    def __init__(self, category_rules: list[BaseRule]) -> None:
        self.category_rules = category_rules

    def classify(self, text: str) -> tuple[str | None, float, list[str], list[str]]:
        hits: list[str] = []
        tags: list[str] = []
        best_category = None
        best_conf = 0.0
        for rule in self.category_rules:
            result = rule.run(text)
            if not result.matched or not result.match:
                continue
            hits.append(rule.meta.rule_id)
            tags.extend(rule.meta.tags)
            # category name from rule_id: cat.<name>
            category = rule.meta.rule_id.split(".", 1)[1]
            if result.match.confidence > best_conf:
                best_category = category
                best_conf = result.match.confidence

        return best_category, best_conf, sorted(set(tags)), hits
