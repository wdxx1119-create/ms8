from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

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
    wm.write_text("\n", encoding="utf-8")
    out_empty_ready = cs._check_m1_short_term_persistence(core, {})
    assert out_empty_ready["status"] == "pass"

    wm.write_text('{"x":1}\n', encoding="utf-8")
    out_ok = cs._check_m1_short_term_persistence(core, {})
    assert out_ok["status"] == "pass"

    workspace_memory = tmp_path / "workspace" / "memory" / "working_memory.jsonl"
    workspace_memory.parent.mkdir(parents=True, exist_ok=True)
    workspace_memory.write_text('{"content":"persisted"}\n', encoding="utf-8")
    missing_legacy = tmp_path / "working_memory.jsonl"
    if missing_legacy.exists():
        missing_legacy.unlink()
    core.working_memory = SimpleNamespace(persistence_file=workspace_memory)
    out_new_path = cs._check_m1_short_term_persistence(core, {})
    assert out_new_path["status"] == "pass"

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


def test_l2_admission_distribution_empty_log_is_initialized(tmp_path: Path) -> None:
    core = _Core(tmp_path)
    p = tmp_path / "auto_memory_pipeline.log"
    p.write_text("", encoding="utf-8")

    out = cs._check_l2_admission_distribution(core, {})

    assert out["status"] == "pass"


def test_m7_m8_branches(tmp_path: Path) -> None:
    core = _Core(tmp_path)

    # m7 missing -> warn
    out_m7_missing = cs._check_m7_kg_access_feedback(core, {})
    assert out_m7_missing["status"] == "warn"

    kg = tmp_path / "knowledge_graph.db"
    conn = sqlite3.connect(kg)
    try:
        conn.execute("CREATE TABLE entities(id INTEGER PRIMARY KEY, access_count INTEGER)")
        rows = [(i, 0) for i in range(1, 61)]
        conn.executemany("INSERT INTO entities(id, access_count) VALUES (?, ?)", rows)
        conn.commit()
    finally:
        conn.close()
    out_m7_warn = cs._check_m7_kg_access_feedback(core, {})
    assert out_m7_warn["status"] == "warn"

    # m8 missing -> warn
    out_m8_missing = cs._check_m8_pipeline_latency_budget(core, {})
    assert out_m8_missing["status"] == "warn"

    plog = tmp_path / "auto_memory_pipeline.log"
    plog.write_text("", encoding="utf-8")
    out_m8_empty = cs._check_m8_pipeline_latency_budget(core, {})
    assert out_m8_empty["status"] == "pass"

    # m8 fail when p95 too high
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
