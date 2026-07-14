"""Rule-first query planning for governed Hybrid Retrieval v1."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .analyzer import QueryAnalysis, analyze_query
from .models import (
    CandidateLimits,
    MemoryQuery,
    Principal,
    QueryIntent,
    RetrievalPlan,
    TimeCoordinates,
)

TemporalKind = Literal["point", "interval"]
TemporalSource = Literal["absolute", "relative"]
ClassifierHook = Callable[[MemoryQuery, QueryAnalysis], QueryIntent | None]

_ISO_DATE_PATTERN = re.compile(
    r"(?<!\d)(?P<date>\d{4}-\d{2}-\d{2})(?:[T\s](?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?(?P<offset>Z|[+-]\d{2}:\d{2})?)?"
)
_CJK_DATE_PATTERN = re.compile(
    r"(?P<year>\d{4})年(?P<month>\d{1,2})月(?P<day>\d{1,2})日(?:\s*(?P<hour>\d{1,2})[:时](?P<minute>\d{1,2})?分?)?"
)
_ENGLISH_AGO_PATTERN = re.compile(r"\b(?P<count>\d+)\s*(?P<unit>hours?|days?|weeks?)\s+ago\b", re.IGNORECASE)
_CJK_AGO_PATTERN = re.compile(r"(?P<count>\d+)\s*(?P<unit>小时|天|周)前")

_HISTORICAL_CUES = (
    "historical",
    "history",
    "previous",
    "formerly",
    "before",
    "why was",
    "why did",
    "曾经",
    "历史",
    "之前",
    "以前",
    "当时",
    "为什么改",
    "为何改",
)
_RULE_CUES = (
    "rule",
    "policy",
    "constraint",
    "requirement",
    "governance",
    "规范",
    "规则",
    "策略",
    "约束",
    "要求",
    "治理",
)
_PREFERENCE_CUES = (
    "preference",
    "prefer",
    "favorite",
    "favourite",
    "like",
    "偏好",
    "喜欢",
    "习惯",
    "倾向",
)
_CURRENT_CUES = (
    "current",
    "currently",
    "now",
    "latest",
    "today",
    "现在",
    "当前",
    "目前",
    "最新",
    "今天",
)


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _contains_any(text: str, values: tuple[str, ...]) -> bool:
    return any(value in text for value in values)


def _timezone(name: str) -> ZoneInfo:
    normalized = str(name or "").strip()
    if not normalized:
        raise ValueError("timezone_name must not be empty")
    try:
        return ZoneInfo(normalized)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown timezone: {normalized}") from exc


def _aware_reference(reference_time: datetime | None, zone: ZoneInfo) -> datetime:
    if reference_time is None:
        return datetime.now(zone)
    if reference_time.tzinfo is None:
        raise ValueError("reference_time must include a timezone")
    return reference_time.astimezone(zone)


def _point_resolution(matched: str, instant: datetime, source: TemporalSource) -> TemporalResolution:
    normalized = _utc_iso(instant)
    return TemporalResolution(
        matched_text=matched,
        kind="point",
        source=source,
        start=normalized,
        end=normalized,
        coordinate_value=normalized,
    )


def _interval_resolution(
    matched: str,
    start: datetime,
    end_exclusive: datetime,
    source: TemporalSource,
) -> TemporalResolution:
    if end_exclusive <= start:
        raise ValueError("temporal interval end must follow start")
    coordinate = end_exclusive - timedelta(microseconds=1)
    return TemporalResolution(
        matched_text=matched,
        kind="interval",
        source=source,
        start=_utc_iso(start),
        end=_utc_iso(end_exclusive),
        coordinate_value=_utc_iso(coordinate),
    )


@dataclass(frozen=True, slots=True)
class TemporalResolution:
    matched_text: str
    kind: TemporalKind
    source: TemporalSource
    start: str
    end: str
    coordinate_value: str

    def __post_init__(self) -> None:
        if not str(self.matched_text or "").strip():
            raise ValueError("temporal matched_text must not be empty")
        if self.kind not in {"point", "interval"}:
            raise ValueError(f"unsupported temporal kind: {self.kind}")
        if self.source not in {"absolute", "relative"}:
            raise ValueError(f"unsupported temporal source: {self.source}")
        # Reuse the Ledger coordinate validator for all serialized instants.
        TimeCoordinates.from_as_of(self.start)
        TimeCoordinates.from_as_of(self.end)
        TimeCoordinates.from_as_of(self.coordinate_value)

    def to_dict(self) -> dict[str, str]:
        return {
            "matched_text": self.matched_text,
            "kind": self.kind,
            "source": self.source,
            "start": self.start,
            "end": self.end,
            "coordinate_value": self.coordinate_value,
        }


@dataclass(frozen=True, slots=True)
class QueryPlanningResult:
    plan: RetrievalPlan
    analysis: QueryAnalysis
    temporal: TemporalResolution | None
    intent_reasons: tuple[str, ...]
    reference_time: str
    timezone_name: str

    def to_dict(self) -> dict[str, object]:
        plan = self.plan
        query = plan.query
        principal = plan.principal
        return {
            "plan": {
                "query": {
                    "text": query.text,
                    "purpose": query.purpose,
                    "time": {
                        "recorded_as_of": query.time.recorded_as_of,
                        "observed_as_of": query.time.observed_as_of,
                        "valid_at": query.time.valid_at,
                    },
                    "realm_ids": list(query.realm_ids),
                    "scope": query.scope,
                },
                "principal": {
                    "principal_id": principal.principal_id,
                    "kind": principal.kind,
                    "realm_ids": list(principal.realm_ids),
                    "scopes": list(principal.scopes),
                    "allowed_sensitivities": list(principal.allowed_sensitivities),
                    "capabilities": list(principal.capabilities),
                },
                "intent": plan.intent,
                "realm_ids": list(plan.realm_ids),
                "language_profile": list(plan.language_profile),
                "entity_mentions": list(plan.entity_mentions),
                "candidate_limits": {
                    "lexical": plan.candidate_limits.lexical,
                    "vector": plan.candidate_limits.vector,
                    "entity": plan.candidate_limits.entity,
                    "temporal": plan.candidate_limits.temporal,
                    "graph": plan.candidate_limits.graph,
                },
                "context_budget_tokens": plan.context_budget_tokens,
            },
            "analysis": self.analysis.to_dict(),
            "temporal": self.temporal.to_dict() if self.temporal is not None else None,
            "intent_reasons": list(self.intent_reasons),
            "reference_time": self.reference_time,
            "timezone": self.timezone_name,
        }


def resolve_temporal_expression(
    text: str,
    *,
    reference_time: datetime,
    timezone_name: str,
) -> TemporalResolution | None:
    zone = _timezone(timezone_name)
    reference = _aware_reference(reference_time, zone)
    original = str(text or "")
    normalized = original.casefold()

    iso_match = _ISO_DATE_PATTERN.search(original)
    if iso_match is not None:
        matched = iso_match.group(0)
        date_value = iso_match.group("date")
        hour = iso_match.group("hour")
        if hour is None:
            local = datetime.fromisoformat(date_value).replace(
                hour=23,
                minute=59,
                second=59,
                microsecond=999999,
                tzinfo=zone,
            )
        else:
            minute = int(iso_match.group("minute") or "0")
            second = int(iso_match.group("second") or "0")
            offset = iso_match.group("offset")
            candidate = f"{date_value}T{int(hour):02d}:{minute:02d}:{second:02d}"
            if offset:
                candidate += "+00:00" if offset == "Z" else offset
                local = datetime.fromisoformat(candidate).astimezone(zone)
            else:
                local = datetime.fromisoformat(candidate).replace(tzinfo=zone)
        return _point_resolution(matched, local, "absolute")

    cjk_match = _CJK_DATE_PATTERN.search(original)
    if cjk_match is not None:
        has_clock = cjk_match.group("hour") is not None
        hour = int(cjk_match.group("hour") or "23")
        minute = int(cjk_match.group("minute") or ("0" if has_clock else "59"))
        second = 0 if has_clock else 59
        microsecond = 0 if has_clock else 999999
        local = datetime(
            int(cjk_match.group("year")),
            int(cjk_match.group("month")),
            int(cjk_match.group("day")),
            hour,
            minute,
            second,
            microsecond,
            tzinfo=zone,
        )
        return _point_resolution(cjk_match.group(0), local, "absolute")

    english_ago = _ENGLISH_AGO_PATTERN.search(normalized)
    if english_ago is not None:
        count = int(english_ago.group("count"))
        unit = english_ago.group("unit").casefold()
        delta = {
            "hour": timedelta(hours=count),
            "hours": timedelta(hours=count),
            "day": timedelta(days=count),
            "days": timedelta(days=count),
            "week": timedelta(weeks=count),
            "weeks": timedelta(weeks=count),
        }[unit]
        return _point_resolution(english_ago.group(0), reference - delta, "relative")

    cjk_ago = _CJK_AGO_PATTERN.search(original)
    if cjk_ago is not None:
        count = int(cjk_ago.group("count"))
        unit = cjk_ago.group("unit")
        delta = {
            "小时": timedelta(hours=count),
            "天": timedelta(days=count),
            "周": timedelta(weeks=count),
        }[unit]
        return _point_resolution(cjk_ago.group(0), reference - delta, "relative")

    if "last week" in normalized or "上周" in original:
        current_week_start = (reference - timedelta(days=reference.weekday())).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        return _interval_resolution(
            "上周" if "上周" in original else "last week",
            current_week_start - timedelta(weeks=1),
            current_week_start,
            "relative",
        )

    if "last month" in normalized or "上个月" in original or "上月" in original:
        current_month_start = reference.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        previous_month_end = current_month_start
        previous_month_start = (current_month_start - timedelta(days=1)).replace(day=1)
        matched = "上个月" if "上个月" in original else ("上月" if "上月" in original else "last month")
        return _interval_resolution(matched, previous_month_start, previous_month_end, "relative")

    if "yesterday" in normalized or "昨天" in original:
        return _point_resolution("昨天" if "昨天" in original else "yesterday", reference - timedelta(days=1), "relative")
    if "today" in normalized or "今天" in original or "now" in normalized or "现在" in original:
        matched = "今天" if "今天" in original else ("现在" if "现在" in original else ("today" if "today" in normalized else "now"))
        return _point_resolution(matched, reference, "relative")
    return None


def _intent_from_rules(query: MemoryQuery, analysis: QueryAnalysis) -> tuple[QueryIntent, tuple[str, ...]]:
    text = analysis.normalized_text
    if query.purpose == "historical":
        return "historical_reason", ("purpose=historical",)
    if analysis.code_tokens:
        return "code_symbol", ("exact_code_token",)
    if _contains_any(text, _HISTORICAL_CUES):
        return "historical_reason", ("historical_cue",)
    if _contains_any(text, _RULE_CUES):
        return "project_rule", ("rule_cue",)
    if _contains_any(text, _PREFERENCE_CUES):
        return "personal_preference", ("preference_cue",)
    if _contains_any(text, _CURRENT_CUES):
        return "current_state", ("current_state_cue",)
    return "open_recall", ("default_open_recall",)


def _entity_mentions(analysis: QueryAnalysis) -> tuple[str, ...]:
    values: list[str] = [*analysis.exact_tokens]
    values.extend(token for token in analysis.cjk_tokens if len(token) > 1)
    values.extend(token for token in analysis.identifier_parts if len(token) > 2)
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
        if len(result) >= 32:
            break
    return tuple(result)


class QueryPlanner:
    """Build deterministic retrieval plans before optional classifier refinement."""

    def __init__(self, *, timezone_name: str = "UTC", classifier: ClassifierHook | None = None) -> None:
        _timezone(timezone_name)
        self.timezone_name = timezone_name
        self.classifier = classifier

    def plan(
        self,
        query: MemoryQuery,
        principal: Principal,
        *,
        reference_time: datetime | None = None,
        candidate_limits: CandidateLimits | None = None,
        context_budget_tokens: int = 1200,
    ) -> QueryPlanningResult:
        if not isinstance(query, MemoryQuery):
            raise TypeError("query must be MemoryQuery")
        if not isinstance(principal, Principal):
            raise TypeError("principal must be Principal")
        zone = _timezone(self.timezone_name)
        reference = _aware_reference(reference_time, zone)
        analysis = analyze_query(query.text)
        temporal = resolve_temporal_expression(
            query.text,
            reference_time=reference,
            timezone_name=self.timezone_name,
        )

        has_explicit_coordinates = any(
            value is not None
            for value in (
                query.time.recorded_as_of,
                query.time.observed_as_of,
                query.time.valid_at,
            )
        )
        planned_time = query.time
        if temporal is not None and not has_explicit_coordinates:
            planned_time = TimeCoordinates.from_as_of(temporal.coordinate_value)

        planned_query = MemoryQuery(
            text=query.text,
            purpose=query.purpose,
            time=planned_time,
            realm_ids=query.realm_ids,
            scope=query.scope,
        )
        intent, reasons = _intent_from_rules(planned_query, analysis)
        if intent == "open_recall" and self.classifier is not None:
            classified = self.classifier(planned_query, analysis)
            if classified is not None:
                allowed = {
                    "current_state",
                    "historical_reason",
                    "project_rule",
                    "personal_preference",
                    "code_symbol",
                    "open_recall",
                }
                if classified not in allowed:
                    raise ValueError(f"classifier returned unsupported intent: {classified}")
                intent = cast(QueryIntent, classified)
                reasons = (*reasons, "classifier_extension")

        if planned_query.realm_ids:
            unauthorized = set(planned_query.realm_ids).difference(principal.realm_ids)
            if unauthorized:
                raise PermissionError("query requested a realm outside the principal boundary")
            realm_ids = planned_query.realm_ids
        else:
            realm_ids = principal.realm_ids

        plan = RetrievalPlan(
            query=planned_query,
            principal=principal,
            intent=intent,
            realm_ids=realm_ids,
            language_profile=analysis.language_profile,
            entity_mentions=_entity_mentions(analysis),
            candidate_limits=candidate_limits or CandidateLimits(),
            context_budget_tokens=context_budget_tokens,
        )
        if temporal is not None:
            reasons = (*reasons, f"temporal={temporal.source}:{temporal.kind}")
        if has_explicit_coordinates:
            reasons = (*reasons, "explicit_time_coordinates_preserved")
        return QueryPlanningResult(
            plan=plan,
            analysis=analysis,
            temporal=temporal,
            intent_reasons=reasons,
            reference_time=_utc_iso(reference),
            timezone_name=self.timezone_name,
        )

    def build_plan(
        self,
        query: MemoryQuery,
        principal: Principal,
        *,
        reference_time: datetime | None = None,
        candidate_limits: CandidateLimits | None = None,
        context_budget_tokens: int = 1200,
    ) -> RetrievalPlan:
        return self.plan(
            query,
            principal,
            reference_time=reference_time,
            candidate_limits=candidate_limits,
            context_budget_tokens=context_budget_tokens,
        ).plan


__all__ = [
    "ClassifierHook",
    "QueryPlanner",
    "QueryPlanningResult",
    "TemporalResolution",
    "resolve_temporal_expression",
]
