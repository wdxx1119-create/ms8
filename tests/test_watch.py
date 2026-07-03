import io
from urllib.parse import unquote

from ms8 import watch


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

    snapshot = watch._self_check_snapshot(payload)

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

    snapshot = watch._self_check_snapshot(payload)

    assert snapshot == {
        "status": "warn",
        "pass": 1,
        "warn": 2,
        "fail": 1,
        "error": 1,
        "warn_ids": [],
        "fail_ids": [],
    }


def test_doctor_follow_up_actions_parses_and_dedupes() -> None:
    output = (
        "MS8 Doctor\n"
        "watch next: ms8 ops governance\n"
        "watch also: ms8 absorb add <directory>\n"
        "watch also: ms8 ops governance\n"
    )

    actions = watch._doctor_follow_up_actions(output)

    assert actions == ["ms8 ops governance", "ms8 absorb add <directory>"]


def test_run_watch_once_emits_encoded_next_actions(monkeypatch, capsys) -> None:
    monkeypatch.setattr(watch, "ensure_runtime_dirs", lambda: {"root": "."})
    monkeypatch.setattr(watch, "run_doctor", lambda: (print("watch next: ms8 ops governance"), 1)[1])
    monkeypatch.setattr(watch, "count_memories", lambda: 3)
    monkeypatch.setattr(watch, "run_daily_learning", lambda: {"ran": True})
    monkeypatch.setattr(watch, "run_kg_batch_extract", lambda limit=20, force=False: {"ran": False})
    monkeypatch.setattr(watch, "run_memory_tiering", lambda: {"ran": False})
    monkeypatch.setattr(watch, "run_graph_maintenance", lambda: {"ran": False})
    monkeypatch.setattr(watch, "run_reflection", lambda: {"ran": False})
    monkeypatch.setattr(watch, "run_synthetic_auto_confirm", lambda: {"ran": False})
    monkeypatch.setattr(
        watch,
        "run_engine_self_check",
        lambda level="L2": {"status": "ok", "summary": {"pass": 1, "warn": 0, "fail": 0, "error": 0}, "results": []},
    )
    monkeypatch.setattr(watch, "absorb_health_summary", lambda: {"risk": "green", "pending_review": 0, "quarantine": 0})
    monkeypatch.setattr(watch, "run_maintenance_now", lambda force=True: {"ok": True, "ran": True})
    monkeypatch.setattr(watch, "repair_compression_if_stale", lambda: {"ran": False})
    monkeypatch.setattr(watch, "repair_duplicates_after_compression", lambda: {"ok": True, "result": {"status": "skipped"}})
    monkeypatch.setattr(watch, "has_recent_activity", lambda window_seconds=30: True)

    code = watch.run_watch(once=True)
    out = capsys.readouterr().out

    assert code == 1
    assert "watch next: ms8 ops governance" in out
    tick_line = next(line for line in out.splitlines() if line.startswith("watch tick:"))
    encoded = tick_line.split("next_actions=", 1)[1].split(" ", 1)[0]
    assert [unquote(item) for item in encoded.split("|")] == ["ms8 ops governance"]


def test_safe_stdout_write_replaces_unencodable_characters(monkeypatch) -> None:
    class FakeStdout(io.StringIO):
        encoding = "ascii"

        def flush(self) -> None:
            return None

    fake_stdout = FakeStdout()
    monkeypatch.setattr(watch.sys, "stdout", fake_stdout)

    watch._safe_stdout_write("watch next: caf\xe9\n")

    assert fake_stdout.getvalue() == "watch next: caf?\n"
