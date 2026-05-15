from __future__ import annotations

from ms8.app.rules.base import BaseRule
from ms8.app.rules.category_rules import build_category_rules


class RuleRegistry:
    def __init__(self) -> None:
        self.preprocess = ["normalize", "strip_code"]
        self.block = ["noise", "ack"]
        self.privacy = ["pii"]
        self.category: list[BaseRule] = build_category_rules()
        self.extraction = ["signals"]
        self.tag = ["derive_aux_tags"]
        self.dedupe = ["hash_dedupe"]
        self.conflict = ["mutual_exclusion"]

    def category_rules_sorted(self) -> list[BaseRule]:
        return sorted(self.category, key=lambda r: r.meta.priority)
