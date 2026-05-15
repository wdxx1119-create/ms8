from __future__ import annotations

from ms8.doctor import _normalize_self_check_payload


def test_normalize_self_check_payload_new_schema_status_mapping() -> None:
    raw = {
        "schema_version": "1.0",
        "status": "failed",
        "summary": {"total": 4, "pass": 3, "warn": 0, "fail": 1, "error": 0, "exit_code": 2},
        "results": [{"status": "pass"}, {"status": "pass"}, {"status": "pass"}, {"status": "fail"}],
    }
    out = _normalize_self_check_payload(raw)
    assert out["schema_version"] == "1.0"
    assert out["status"] == "fail"
    assert out["summary"]["total"] == 4


def test_normalize_self_check_payload_derives_summary_from_results() -> None:
    raw = {
        "status": "ok",
        "results": [{"status": "pass"}, {"status": "warn"}, {"status": "error"}],
    }
    out = _normalize_self_check_payload(raw)
    assert out["summary"]["total"] == 3
    assert out["summary"]["pass"] == 1
    assert out["summary"]["warn"] == 1
    assert out["summary"]["error"] == 1
    # summary wins for severity even if raw status says ok
    assert out["summary"]["exit_code"] == 2


def test_normalize_self_check_payload_invalid_payload() -> None:
    out = _normalize_self_check_payload("bad")  # type: ignore[arg-type]
    assert out["status"] == "error"
    assert out["summary"]["exit_code"] == 2

