from ms8.watch import _self_check_snapshot


def test_self_check_snapshot_normalizes_wrapped_failed_status():
    payload = {
        "ok": True,
        "result": {
            "status": "failed",
            "summary": {"pass": 3, "warn": 1, "fail": 2, "error": 0},
            "results": [
                {"status": "warn", "check_id": "warn_a"},
                {"status": "fail", "check_id": "fail_a"},
                {"status": "error", "check_id": "fail_b"},
            ],
        },
    }

    snapshot = _self_check_snapshot(payload)

    assert snapshot == {
        "status": "fail",
        "pass": 3,
        "warn": 1,
        "fail": 2,
        "error": 0,
        "warn_ids": ["warn_a"],
        "fail_ids": ["fail_a", "fail_b"],
    }


def test_self_check_snapshot_counts_rows_when_summary_missing():
    payload = {
        "status": "warning",
        "results": [
            {"status": "pass"},
            {"status": "warn"},
            {"status": "fail"},
            {"status": "error"},
            {"status": "warn"},
        ],
    }

    snapshot = _self_check_snapshot(payload)

    assert snapshot == {
        "status": "warn",
        "pass": 1,
        "warn": 2,
        "fail": 1,
        "error": 1,
        "warn_ids": [],
        "fail_ids": [],
    }
