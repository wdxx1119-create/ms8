from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from ms8.memory.retrieval import (
    MemoryQuery,
    Principal,
    QueryPlanner,
    TimeCoordinates,
    analyze_query,
    resolve_temporal_expression,
)


def _principal() -> Principal:
    return Principal(
        principal_id="user:test",
        kind="user",
        realm_ids=("project:ms8", "personal"),
        scopes=("project", "personal"),
        allowed_sensitivities=("public", "internal", "private"),
        capabilities=("all",),
    )


def test_analyzer_preserves_commands_paths_versions_calls_and_cpp() -> None:
    analysis = analyze_query(
        '请检查 "C:\\Program Files\\MS8\\foo_bar.py" 和 ./src/ms8/queryPlanner.py，'
        "用 python -m ms8 --explain 调用 prepare_reply()，版本 v0.2.18，支持 C++"
    )

    assert "C:\\Program Files\\MS8\\foo_bar.py" in analysis.exact_tokens
    assert "./src/ms8/queryPlanner.py" in analysis.exact_tokens
    assert "--explain" in analysis.exact_tokens
    assert "prepare_reply()" in analysis.exact_tokens
    assert "v0.2.18" in analysis.exact_tokens
    assert "C++" in analysis.exact_tokens
    assert {"query", "planner", "prepare", "reply", "foo", "bar"}.issubset(
        set(analysis.identifier_parts)
    )
    assert analysis.language_profile == ("zh", "en", "code")


def test_rule_first_planner_resolves_historical_relative_time() -> None:
    reference = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)
    result = QueryPlanner(timezone_name="UTC").plan(
        MemoryQuery(text="昨天的项目规则为什么改变", realm_ids=("project:ms8",), scope="project"),
        _principal(),
        reference_time=reference,
    )

    assert result.plan.intent == "historical_reason"
    assert result.temporal is not None
    assert result.temporal.source == "relative"
    assert result.temporal.kind == "point"
    assert result.temporal.coordinate_value == "2026-07-12T12:00:00Z"
    assert result.plan.query.time == TimeCoordinates.from_as_of("2026-07-12T12:00:00Z")
    assert "historical_cue" in result.intent_reasons


def test_absolute_date_and_last_week_are_explicit_and_explainable() -> None:
    reference = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)
    absolute = resolve_temporal_expression(
        "截至 2026-07-01 的规则",
        reference_time=reference,
        timezone_name="UTC",
    )
    assert absolute is not None
    assert absolute.kind == "point"
    assert absolute.source == "absolute"
    assert absolute.coordinate_value == "2026-07-01T23:59:59.999999Z"

    interval = resolve_temporal_expression(
        "last week project decisions",
        reference_time=reference,
        timezone_name="UTC",
    )
    assert interval is not None
    assert interval.kind == "interval"
    assert interval.start == "2026-07-06T00:00:00Z"
    assert interval.end == "2026-07-13T00:00:00Z"
    assert interval.coordinate_value == "2026-07-12T23:59:59.999999Z"


def test_explicit_time_coordinates_are_not_overwritten_by_text() -> None:
    explicit = TimeCoordinates.from_as_of("2026-06-01T00:00:00Z")
    result = QueryPlanner(timezone_name="UTC").plan(
        MemoryQuery(
            text="昨天的项目规则",
            time=explicit,
            realm_ids=("project:ms8",),
            scope="project",
        ),
        _principal(),
        reference_time=datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc),
    )

    assert result.temporal is not None
    assert result.plan.query.time == explicit
    assert "explicit_time_coordinates_preserved" in result.intent_reasons


def test_code_and_preference_intents_and_classifier_extension() -> None:
    planner = QueryPlanner(timezone_name="UTC")
    code = planner.plan(
        MemoryQuery(text="where is prepare_reply() in ./src/ms8", realm_ids=("project:ms8",)),
        _principal(),
        reference_time=datetime(2026, 7, 13, tzinfo=timezone.utc),
    )
    assert code.plan.intent == "code_symbol"

    preference = planner.plan(
        MemoryQuery(text="我偏好哪种发布方式", realm_ids=("personal",), scope="personal"),
        _principal(),
        reference_time=datetime(2026, 7, 13, tzinfo=timezone.utc),
    )
    assert preference.plan.intent == "personal_preference"

    classified = QueryPlanner(
        timezone_name="UTC",
        classifier=lambda _query, _analysis: "current_state",
    ).plan(
        MemoryQuery(text="tell me about ms8", realm_ids=("project:ms8",)),
        _principal(),
        reference_time=datetime(2026, 7, 13, tzinfo=timezone.utc),
    )
    assert classified.plan.intent == "current_state"
    assert "classifier_extension" in classified.intent_reasons


def test_planning_result_is_json_serializable_and_rejects_unauthorized_realm() -> None:
    planner = QueryPlanner(timezone_name="Asia/Tokyo")
    result = planner.plan(
        MemoryQuery(text="current project rule", realm_ids=("project:ms8",), scope="project"),
        _principal(),
        reference_time=datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc),
    )
    payload = result.to_dict()
    assert json.loads(json.dumps(payload, ensure_ascii=False))["plan"]["intent"] == "project_rule"
    assert payload["timezone"] == "Asia/Tokyo"

    with pytest.raises(PermissionError, match="outside the principal boundary"):
        planner.plan(
            MemoryQuery(text="current secret", realm_ids=("secret",)),
            _principal(),
            reference_time=datetime(2026, 7, 13, tzinfo=timezone.utc),
        )
