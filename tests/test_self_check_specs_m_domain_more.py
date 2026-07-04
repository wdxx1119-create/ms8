from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ms8.engine_core.maintenance.self_check import check_specs as cs


class _Core:
    def __init__(self, memory_dir: Path) -> None:
        self.config = {
            "memory_dir": str(memory_dir),
            "settings": {"memory": {"security": {}, "connect": {}}},
        }
        self._full_hits = [{"id": "1"}]
        self._part_hits = [{"id": "2"}]

    def retrieve_memories(self, query: str, top_k: int = 5):  # noqa: ANN001
        if query == "记忆系统配置":
            return self._full_hits
        if query == "记忆":
            return self._part_hits
        return []


def _write_json(path: Path, payload: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_m1_m2_m3_branches(tmp_path: Path) -> None:
    core = _Core(tmp_path)

    # m1: missing/empty/no-api/healthy
    out_missing = cs._check_m1_short_term_persistence(core, {})
    assert out_missing["status"] == "warn"

    wm = tmp_path / "working_memory.jsonl"
    wm.write_text("\n", encoding="utf-8")
    out_empty = cs._check_m1_short_term_persistence(core, {})
    assert out_empty["status"] == "warn"

    wm.write_text('{"x":1}\n', encoding="utf-8")
    out_no_api = cs._check_m1_short_term_persistence(core, {})
    assert out_no_api["status"] == "warn"

    setattr(core, "restore_short_term_by_topic", lambda *args, **kwargs: None)
    out_ok = cs._check_m1_short_term_persistence(core, {})
    assert out_ok["status"] == "pass"

    # m2: leaked rejected -> fail, then pass.
    idx = tmp_path / "auto_memory_index.json"
    _write_json(idx, [{"status": "rejected", "excluded": False}, {"status": "accepted"}])
    out_leak = cs._check_m2_rejected_not_indexed(core, {})
    assert out_leak["status"] == "fail"

    _write_json(idx, [{"status": "rejected", "excluded": True}, {"status": "accepted"}])
    out_clean = cs._check_m2_rejected_not_indexed(core, {})
    assert out_clean["status"] == "pass"

    # m3: backlog fail and healthy
    q = tmp_path / "auto_memory_review_queue.jsonl"
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
    q.write_text(json.dumps({"decision": "pending", "timestamp": old_ts}) + "\n", encoding="utf-8")
    out_fail = cs._check_m3_review_queue_sla(core, {})
    assert out_fail["status"] == "fail"

    fresh_ts = datetime.now(timezone.utc).isoformat()
    q.write_text(json.dumps({"decision": "pending", "timestamp": fresh_ts}) + "\n", encoding="utf-8")
    out_ok2 = cs._check_m3_review_queue_sla(core, {})
    assert out_ok2["status"] == "pass"


def test_m4_m5_m6_branches(tmp_path: Path) -> None:
    core = _Core(tmp_path)
    now = datetime.now(timezone.utc).isoformat()

    # m4 no samples -> pass
    out_m4_empty = cs._check_m4_dedupe_false_positive_probe(core, {})
    assert out_m4_empty["status"] == "pass"

    p = tmp_path / "auto_memory_pipeline.log"
    p.write_text(
        "\n".join(
            [
                json.dumps({"dropped": [{"reason": "duplicate"}], "confidence": 0.9}),
                json.dumps({"dropped": [{"reason": "duplicate"}], "confidence": 0.95}),
                json.dumps({"dropped": [{"reason": "duplicate"}], "confidence": 0.1}),
                json.dumps({"dropped": [{"reason": "other"}], "confidence": 0.9}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    out_m4_warn = cs._check_m4_dedupe_false_positive_probe(core, {})
    assert out_m4_warn["status"] in {"warn", "pass"}

    # m5 unreadable/fail/pass
    cache = tmp_path / "semantic_cache.json"
    cache.write_text("{", encoding="utf-8")
    out_m5_bad = cs._check_m5_semantic_cache_health(core, {})
    assert out_m5_bad["status"] == "warn"

    _write_json(cache, {"items": [{"dense": None}, {"dense": []}, {"dense": [0.1]}]})
    out_m5_fail = cs._check_m5_semantic_cache_health(core, {})
    assert out_m5_fail["status"] == "fail"

    _write_json(cache, {"items": [{"dense": [0.1, 0.2]}, {"dense": [0.3]}]})
    out_m5_ok = cs._check_m5_semantic_cache_health(core, {})
    assert out_m5_ok["status"] == "pass"

    # m6 weak and no-hit branches
    core._full_hits = []
    core._part_hits = [{"id": "part"}]
    out_m6_weak = cs._check_m6_cjk_recall_probe(core, {})
    assert out_m6_weak["status"] == "warn"

    core._part_hits = []
    out_m6_none = cs._check_m6_cjk_recall_probe(core, {})
    assert out_m6_none["status"] == "warn"

    core._full_hits = [{"id": "f"}]
    core._part_hits = [{"id": "p"}]
    out_m6_ok = cs._check_m6_cjk_recall_probe(core, {})
    assert out_m6_ok["status"] == "pass"

    # keep pipeline log timestamp fresh for m8 later.
    p.write_text(json.dumps({"duration_ms": 10.0, "timestamp": now}) + "\n", encoding="utf-8")


def test_m7_m8_branches(tmp_path: Path) -> None:
    core = _Core(tmp_path)

    # m7 missing -> warn
    out_m7_missing = cs._check_m7_kg_access_feedback(core, {})
    assert out_m7_missing["status"] == "warn"

    kg = tmp_path / "knowledge_graph.db"
    with closing(sqlite3.connect(kg)) as conn:
        conn.execute("CREATE TABLE entities(id INTEGER PRIMARY KEY, access_count INTEGER)")
        rows = [(i, 0) for i in range(1, 61)]
        conn.executemany("INSERT INTO entities(id, access_count) VALUES (?, ?)", rows)
        conn.commit()
    out_m7_warn = cs._check_m7_kg_access_feedback(core, {})
    assert out_m7_warn["status"] == "warn"

    # m8 missing -> warn
    out_m8_missing = cs._check_m8_pipeline_latency_budget(core, {})
    assert out_m8_missing["status"] == "warn"

    # m8 fail when p95 too high
    plog = tmp_path / "auto_memory_pipeline.log"
    plog.write_text(
        "\n".join([json.dumps({"duration_ms": 4000.0}), json.dumps({"duration_ms": 3500.0})]) + "\n",
        encoding="utf-8",
    )
    out_m8_fail = cs._check_m8_pipeline_latency_budget(core, {})
    assert out_m8_fail["status"] == "fail"

    # m8 pass with small samples
    plog.write_text(
        "\n".join([json.dumps({"duration_ms": 100.0}), json.dumps({"trace": {"duration_ms": 120.0}})]) + "\n",
        encoding="utf-8",
    )
    out_m8_ok = cs._check_m8_pipeline_latency_budget(core, {})
    assert out_m8_ok["status"] == "pass"


def test_m11_m12_m13_branches(tmp_path: Path, monkeypatch) -> None:
    core = _Core(tmp_path)
    core.config["workspace_dir"] = str(tmp_path)
    core.config["settings"]["memory"]["auto_memory"] = {
        "session_ingestion": {
            "state_file": "memory/openclaw_session_ingest_state.json",
            "lock_stale_seconds": 1,
        }
    }

    project_row = {"name": "demo", "root": str(tmp_path / "demo")}
    monkeypatch.setattr("ms8.absorb.project_memory.scope.list_projects", lambda: [project_row])
    monkeypatch.setattr(
        "ms8.absorb.project_memory.health.project_status",
        lambda **_kwargs: {
            "db_query_ok": True,
            "index_status": "ready",
            "changed_files_pending": 0,
            "build_last_error": "",
            "watch_state": {"last_error": ""},
            "service_state": {"installed": True, "running": False},
            "recommended_runtime_mode": "foreground_watch",
            "foreground_watch_available": True,
        },
    )
    out_pm_warn = cs._check_m11_project_memory_health(core, {})
    assert out_pm_warn["status"] == "pass"

    monkeypatch.setattr("ms8.absorb.project_memory.scope.list_projects", lambda: [])
    out_pm_empty = cs._check_m11_project_memory_health(core, {})
    assert out_pm_empty["status"] == "pass"

    core.run_validation_suite = lambda: {  # type: ignore[attr-defined]
        "status": "error",
        "ok": False,
        "message": "validation suite contains no executable tests",
        "total_tests": 0,
        "passed": 0,
        "failed": 0,
    }
    out_vs_warn = cs._check_m12_validation_suite_runtime(core, {})
    assert out_vs_warn["status"] == "warn"

    core.run_validation_suite = lambda: {  # type: ignore[attr-defined]
        "status": "success",
        "ok": True,
        "message": "validation suite passed",
        "total_tests": 1,
        "passed": 1,
        "failed": 0,
    }
    out_vs_ok = cs._check_m12_validation_suite_runtime(core, {})
    assert out_vs_ok["status"] == "pass"

    class _Auto:
        session_state_file = tmp_path / "memory" / "openclaw_session_ingest_state.json"
        session_lock_dir = tmp_path / "memory" / "openclaw_session_ingest_state.json.lock"
        session_lock_info_file = session_lock_dir / "owner.json"
        session_lock_stale_seconds = 1

        @staticmethod
        def _pid_alive(_pid: int) -> bool:
            return False

    core.auto_memory = _Auto()  # type: ignore[attr-defined]
    _Auto.session_state_file.parent.mkdir(parents=True, exist_ok=True)
    _Auto.session_state_file.write_text("{", encoding="utf-8")
    out_sync_bad = cs._check_m13_session_sync_health(core, {})
    assert out_sync_bad["status"] == "warn"

    _Auto.session_state_file.write_text(
        json.dumps({"files": {}, "recent_hashes": [], "last_sync_at": ""}),
        encoding="utf-8",
    )
    _Auto.session_lock_dir.mkdir(parents=True, exist_ok=True)
    _Auto.session_lock_info_file.write_text(
        json.dumps({"pid": 999999, "started_at": "2000-01-01T00:00:00+00:00"}),
        encoding="utf-8",
    )
    out_sync_stale = cs._check_m13_session_sync_health(core, {})
    assert out_sync_stale["status"] == "warn"
