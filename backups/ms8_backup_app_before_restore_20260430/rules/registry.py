from __future__ import annotations

from typing import List

from app.rules.base import BaseRule
from app.rules.category_rules import build_category_rules


class RuleRegistry:
    def __init__(self) -> None:
        self.preprocess = ["normalize", "strip_code"]
        self.block = ["noise", "ack"]
        self.privacy = ["pii"]
        self.category: List[BaseRule] = build_category_rules()
        self.extraction = ["signals"]
        self.tag = ["derive_aux_tags"]
        self.dedupe = ["hash_dedupe"]
        self.conflict = ["mutual_exclusion"]

    def category_rules_sorted(self) -> List[BaseRule]:
        return sorted(self.category, key=lambda r: r.meta.priority)
