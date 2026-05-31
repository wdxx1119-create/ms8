from __future__ import annotations

from ms8 import doctor


def test_format_trend_delta_with_values() -> None:
    text = doctor._format_trend_delta(
        {
            "risk": "yellow",
            "delta": {
                "noncanonical_records": 1,
                "schema_invalid_count": -2,
                "fallback_write_count": 0,
                "fallback_total_count": 3,
                "fallback_error_code_spike": 0,
                "duplicate_groups": -1,
                "pending_review": 4,
            },
        }
    )
    assert "risk=yellow" in text
    assert "noncanonical=1" in text
    assert "schema_invalid=-2" in text
    assert "fallback_total=3" in text
    assert "dup_groups=-1" in text
    assert "pending_review=4" in text


def test_format_trend_delta_empty() -> None:
    assert doctor._format_trend_delta({}) == "risk=green delta=n/a"
    assert doctor._format_trend_delta("x") == "risk=green delta=n/a"  # type: ignore[arg-type]


def test_normalize_self_check_payload_with_legacy_shape() -> None:
    out = doctor._normalize_self_check_payload(
        {
            "summary": {"total_checks": 3, "passed_checks": 2, "failed_checks": 0, "warnings": 1},
            "results": [{"status": "pass"}, {"status": "pass"}, {"status": "warn"}],
        }
    )
    assert out["summary"]["total"] == 3
    assert out["summary"]["pass"] == 2
    assert out["summary"]["warn"] == 1
    assert out["summary"]["exit_code"] == 1


def test_normalize_self_check_payload_status_fallback() -> None:
    out = doctor._normalize_self_check_payload(
        {
            "status": "failed",
            "summary": {"total": 1, "pass": 0, "warn": 0, "fail": 1, "error": 0},
            "results": [{"check_id": "x", "status": "fail"}],
        }
    )
    assert out["status"] == "fail"
    assert out["summary"]["fail"] == 1
