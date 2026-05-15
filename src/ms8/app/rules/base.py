from __future__ import annotations

import re
from dataclasses import dataclass

from ms8.app.schemas.pipeline_schema import RuleMatch, RuleMeta


@dataclass
class RuleResult:
    matched: bool
    match: RuleMatch | None = None


class BaseRule:
    def __init__(self, meta: RuleMeta) -> None:
        self.meta = meta

    def applies(self, text: str) -> bool:
        if not self.meta.enabled:
            return False
        for neg in self.meta.negative_patterns:
            if re.search(neg, text, flags=re.IGNORECASE):
                return False
        if not self.meta.patterns:
            return False
        return any(re.search(p, text, flags=re.IGNORECASE) for p in self.meta.patterns)

    def run(self, text: str) -> RuleResult:
        if not self.applies(text):
            return RuleResult(matched=False)
        match = RuleMatch(
            rule=self.meta,
            category=None,
            confidence=self.meta.confidence,
            reason=f"rule:{self.meta.rule_id}",
            payload={},
        )
        return RuleResult(matched=True, match=match)


def build_meta(data: dict) -> RuleMeta:
    return RuleMeta(
        rule_id=str(data["rule_id"]),
        priority=int(data.get("priority", 100)),
        confidence=float(data.get("confidence", 0.5)),
        patterns=list(data.get("patterns", [])),
        negative_patterns=list(data.get("negative_patterns", [])),
        signals=list(data.get("signals", [])),
        tags=list(data.get("tags", [])),
        extract_fields=list(data.get("extract_fields", [])),
        enabled=bool(data.get("enabled", True)),
    )
