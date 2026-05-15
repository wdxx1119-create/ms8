from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class RuleMeta:
    rule_id: str
    priority: int
    confidence: float
    patterns: List[str] = field(default_factory=list)
    negative_patterns: List[str] = field(default_factory=list)
    signals: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    extract_fields: List[str] = field(default_factory=list)
    enabled: bool = True


@dataclass
class RuleMatch:
    rule: RuleMeta
    category: Optional[str] = None
    confidence: float = 0.0
    reason: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MemoryRecord:
    text: str
    normalized_text: str
    category: str
    confidence: float
    tags: List[str] = field(default_factory=list)
    entities: List[str] = field(default_factory=list)
    action: str = ""
    object: str = ""
    status: str = "accepted"
    time_info: Dict[str, Any] = field(default_factory=dict)
    matched_rules: List[str] = field(default_factory=list)
    llm_used: bool = False
    needs_review: bool = False
    review_reason: str = ""
    duplicate_of: Optional[str] = None
    conflict_flag: bool = False
    source: str = "interaction"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineContext:
    raw_text: str
    source: str = "interaction"
    traces: List[Dict[str, Any]] = field(default_factory=list)
    flags: Dict[str, Any] = field(default_factory=dict)
    candidates: List[RuleMatch] = field(default_factory=list)
    extracted: Dict[str, Any] = field(default_factory=dict)
    augmented_context: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineResult:
    status: str
    records: List[MemoryRecord] = field(default_factory=list)
    dropped: List[Dict[str, Any]] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    trace_id: str = ""
