from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from ms8.engine_core.maintenance.self_check import check_specs as cs


class _Core:
    def __init__(self, memory_dir: Path) -> None:
        self.config = {"memory_dir": str(memory_dir)}
        self.monitoring = None


def _create_db(path: Path) -> None:
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS t(id INTEGER PRIMARY KEY, v TEXT)")
        conn.commit()


def _create_kg(path: Path, orphan: int = 0) -> None:
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS entities(id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS relations("
            "id INTEGER PRIMARY KEY, subject_entity_id INTEGER, object_entity_id INTEGER)"
        )
        conn.execute("DELETE FROM entities")
        conn.execute("DELETE FROM relations")
        conn.execute("INSERT INTO entities(id, name) VALUES (1, 'a'), (2, 'b')")
        conn.execute("INSERT INTO relations(id, subject_entity_id, object_entity_id) VALUES (1, 1, 2)")
        # Add orphans if requested.
        for i in range(orphan):
            conn.execute(
                "INSERT INTO relations(id, subject_entity_id, object_entity_id) VALUES (?, ?, ?)",
                (10 + i, 999, 2),
            )
        conn.commit()


def test_l2_sqlite_integrity_ok(tmp_path: Path) -> None:
    core = _Core(tmp_path)
    _create_db(tmp_path / "memory.db")
    _create_db(tmp_path / "knowledge_graph.db")
    out = cs._check_l2_sqlite_integrity(core, {})
    assert out["status"] == "pass"


def test_l2_kg_orphan_warn_and_fail(tmp_path: Path) -> None:
    core = _Core(tmp_path)
    kg = tmp_path / "knowledge_graph.db"

    _create_kg(kg, orphan=1)
    warn = cs._check_l2_kg_orphan_check(core, {})
    assert warn["status"] == "warn"

    _create_kg(kg, orphan=7)
    fail = cs._check_l2_kg_orphan_check(core, {})
    assert fail["status"] == "fail"


def test_l2_jsonl_parse_warn(tmp_path: Path) -> None:
    core = _Core(tmp_path)
    p = tmp_path / "auto_memory_records.jsonl"
    p.write_text('{"ok": 1}\nnot-json\n{"ok": 2}\n', encoding="utf-8")
    out = cs._check_l2_jsonl_parse(core, {})
    assert out["status"] == "warn"
    assert out["details"]["bad_lines"] >= 1


def test_l2_slo_check_single_warn_and_skip() -> None:
    class _MonitoringWarn:
        def status(self) -> dict:
            return {"slo": {"all_ok": False, "checks": {"capture": False, "inject": True}}}

    class _MonitoringReplayOnly:
        def status(self) -> dict:
            return {
                "slo": {"all_ok": False, "checks": {"shadow_replay_success_rate": False}},
                "maintenance_policy": {"shadow_replay": {"runs": 0}},
            }

    core_warn = _Core(Path("."))
    core_warn.monitoring = _MonitoringWarn()
    out_warn = cs._check_l2_slo_check(core_warn, {})
    assert out_warn["status"] == "warn"

    core_skip = _Core(Path("."))
    core_skip.monitoring = _MonitoringReplayOnly()
    out_skip = cs._check_l2_slo_check(core_skip, {})
    assert out_skip["status"] == "pass"
