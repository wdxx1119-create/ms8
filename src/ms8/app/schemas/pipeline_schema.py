from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class RuleMeta:
    rule_id: str
    priority: int
    confidence: float
    patterns: list[str] = field(default_factory=list)
    negative_patterns: list[str] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    extract_fields: list[str] = field(default_factory=list)
    enabled: bool = True


@dataclass
class RuleMatch:
    rule: RuleMeta
    category: str | None = None
    confidence: float = 0.0
    reason: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class MemoryRecord:
    text: str
    normalized_text: str
    category: str
    confidence: float
    tags: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    action: str = ""
    object: str = ""
    status: str = "accepted"
    time_info: dict[str, Any] = field(default_factory=dict)
    matched_rules: list[str] = field(default_factory=list)
    llm_used: bool = False
    needs_review: bool = False
    review_reason: str = ""
    duplicate_of: str | None = None
    conflict_flag: bool = False
    source: str = "interaction"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineContext:
    raw_text: str
    source: str = "interaction"
    traces: list[dict[str, Any]] = field(default_factory=list)
    flags: dict[str, Any] = field(default_factory=dict)
    candidates: list[RuleMatch] = field(default_factory=list)
    extracted: dict[str, Any] = field(default_factory=dict)
    augmented_context: dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineResult:
    status: str
    records: list[MemoryRecord] = field(default_factory=list)
    dropped: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    trace_id: str = ""
