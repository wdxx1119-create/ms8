from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ms8.memory.retrieval import MemoryQuery, Principal, QueryPlanner, analyze_query, resolve_temporal_expression


def _principal() -> Principal:
    return Principal(
        principal_id="agent:test",
        kind="agent",
        realm_ids=("project:ms8",),
        scopes=("project",),
        capabilities=("all",),
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2 hours ago", "2026-07-13T10:00:00Z"),
        ("3 days ago", "2026-07-10T12:00:00Z"),
        ("2 weeks ago", "2026-06-29T12:00:00Z"),
        ("2小时前", "2026-07-13T10:00:00Z"),
        ("3天前", "2026-07-10T12:00:00Z"),
        ("2周前", "2026-06-29T12:00:00Z"),
    ],
)
def test_relative_count_expressions(text: str, expected: str) -> None:
    resolved = resolve_temporal_expression(
        text,
        reference_time=datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc),
        timezone_name="UTC",
    )
    assert resolved is not None
    assert resolved.coordinate_value == expected


def test_last_month_and_cjk_absolute_clock() -> None:
    reference = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)
    last_month = resolve_temporal_expression(
        "上个月的发布决定",
        reference_time=reference,
        timezone_name="UTC",
    )
    assert last_month is not None
    assert last_month.kind == "interval"
    assert last_month.start == "2026-06-01T00:00:00Z"
    assert last_month.end == "2026-07-01T00:00:00Z"

    absolute = resolve_temporal_expression(
        "2026年7月1日 9时30分",
        reference_time=reference,
        timezone_name="Asia/Tokyo",
    )
    assert absolute is not None
    assert absolute.coordinate_value == "2026-07-01T00:30:00Z"


def test_explicit_iso_offset_and_current_relative_terms() -> None:
    reference = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)
    offset = resolve_temporal_expression(
        "as of 2026-07-01T09:30:00+09:00",
        reference_time=reference,
        timezone_name="Asia/Tokyo",
    )
    assert offset is not None
    assert offset.coordinate_value == "2026-07-01T00:30:00Z"

    today = resolve_temporal_expression(
        "今天的状态",
        reference_time=reference,
        timezone_name="UTC",
    )
    assert today is not None
    assert today.coordinate_value == "2026-07-13T12:00:00Z"


def test_analyzer_variants_empty_input_and_unknown_profile() -> None:
    analysis = analyze_query("Policies buses class")
    assert "policy" in analysis.english_tokens
    assert "buse" in analysis.english_tokens
    assert "class" in analysis.english_tokens

    symbols = analyze_query("12345")
    assert symbols.language_profile == ("unknown",)

    with pytest.raises(ValueError, match="must not be empty"):
        analyze_query("  ")


def test_planner_validation_and_build_plan_convenience() -> None:
    with pytest.raises(ValueError, match="unknown timezone"):
        QueryPlanner(timezone_name="Invalid/Timezone")

    planner = QueryPlanner(timezone_name="UTC")
    with pytest.raises(ValueError, match="include a timezone"):
        planner.plan(
            MemoryQuery(text="current rule", realm_ids=("project:ms8",)),
            _principal(),
            reference_time=datetime(2026, 7, 13, 12, 0),
        )

    plan = planner.build_plan(
        MemoryQuery(text="current rule", realm_ids=("project:ms8",), scope="project"),
        _principal(),
        reference_time=datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc),
    )
    assert plan.intent == "project_rule"
    assert plan.realm_ids == ("project:ms8",)


def test_classifier_is_only_used_for_open_recall_and_must_return_known_intent() -> None:
    calls: list[str] = []

    def classifier(query: MemoryQuery, _analysis: object) -> str:
        calls.append(query.text)
        return "current_state"

    planner = QueryPlanner(timezone_name="UTC", classifier=classifier)  # type: ignore[arg-type]
    rule = planner.plan(
        MemoryQuery(text="project rule", realm_ids=("project:ms8",)),
        _principal(),
        reference_time=datetime(2026, 7, 13, tzinfo=timezone.utc),
    )
    assert rule.plan.intent == "project_rule"
    assert calls == []

    open_recall = planner.plan(
        MemoryQuery(text="tell me something", realm_ids=("project:ms8",)),
        _principal(),
        reference_time=datetime(2026, 7, 13, tzinfo=timezone.utc),
    )
    assert open_recall.plan.intent == "current_state"
    assert calls == ["tell me something"]

    invalid = QueryPlanner(
        timezone_name="UTC",
        classifier=lambda _query, _analysis: "not-an-intent",  # type: ignore[return-value]
    )
    with pytest.raises(ValueError, match="unsupported intent"):
        invalid.plan(
            MemoryQuery(text="tell me something", realm_ids=("project:ms8",)),
            _principal(),
            reference_time=datetime(2026, 7, 13, tzinfo=timezone.utc),
        )
