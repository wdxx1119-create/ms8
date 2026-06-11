from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

from ms8.engine_core.maintenance.self_check import reporter


def _mk_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("create table if not exists entities(id integer)")
        conn.execute("create table if not exists relations(id integer)")
        conn.execute("create table if not exists memory_anchors(id integer)")
        conn.execute("insert into entities(id) values (1)")
        conn.execute("insert into relations(id) values (1)")
        conn.execute("insert into memory_anchors(id) values (1)")
        conn.commit()
    finally:
        conn.close()


def test_build_health_card_and_diff(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    memory_dir = tmp_path / "mem"
    (workspace / "MEMORY.md").parent.mkdir(parents=True, exist_ok=True)
    (workspace / "MEMORY.md").write_text("abc", encoding="utf-8")
    (workspace / "config.yaml").write_text("k: v", encoding="utf-8")
    (memory_dir / "index" / "whoosh_index").mkdir(parents=True, exist_ok=True)
    (memory_dir / "index" / "whoosh_index" / "a.seg").write_text("x", encoding="utf-8")
    (memory_dir / "auto_memory_records.jsonl").write_text('{"a":1}\n{"b":2}\n', encoding="utf-8")
    (memory_dir / "security" / "shadow_data").mkdir(parents=True, exist_ok=True)
    (memory_dir / "security" / "shadow_data" / "seal_manifest.json").write_text("{}", encoding="utf-8")
    _mk_db(memory_dir / "memory.db")
    _mk_db(memory_dir / "knowledge_graph.db")

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"models":[{"name":"a"}]}'

    monkeypatch.setattr(reporter.urllib.request, "urlopen", lambda *a, **k: _Resp())
    monkeypatch.setattr(
        reporter.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    class _Mon:
        def status(self):
            return {
                "rates": {"capture_rate": 0.9, "injection_rate": 0.8, "duplicate_drop_rate": 0.1},
                "slo": {"all_ok": True},
                "self_check_stats": {"latest_level": "L4", "latest_exit_code": 0, "latest_age_minutes": 5},
                "shadow_runtime_stats": {"sealed": False},
            }

    core = SimpleNamespace(
        monitoring=_Mon(),
        config={
            "workspace_dir": str(workspace),
            "memory_dir": str(memory_dir),
            "settings": {"memory": {"self_check": {"health_card_hash_min_mb": 0.01}, "connect": {}}},
        },
    )
    card = reporter.build_health_card(core, snapshot_reason="unit")
    assert card["services"]["launchd_mcp"] == "running"
    assert card["services"]["ollama_reachable"] is True
    assert card["counts"]["auto_memory_entries"] == 2
    assert card["counts"]["kg_entities"] == 1
    assert card["runtime"]["slo_all_ok"] is True
    assert card["meta"]["snapshot_reason"] == "unit"

    previous = {"meta": {"card_version": 1}}
    diff = reporter._diff_health_card(previous, card)
    assert diff["summary"]["total"] >= 1
    assert any(d["type"] == "migrated" for d in diff["diffs"])


def test_persist_health_card_and_sealed_behavior(tmp_path: Path) -> None:
    mem = tmp_path / "memory"
    card = {"meta": {"snapshot_ts": "2026-01-01T00:00:00+00:00"}}

    # sealed without force -> skipped
    out1 = reporter.persist_health_card(mem, card, sealed=True, force=False)
    assert out1["skipped"] is True
    assert out1["reason"] == "shadow_sealed"

    # write baseline/history
    out2 = reporter.persist_health_card(mem, card, keep_max=1, write_baseline=True, sealed=False)
    assert Path(out2["latest"]).exists()
    assert Path(out2["baseline"]).exists()
    assert Path(out2["baseline_signature"]).exists()
    assert out2["baseline_written"] is True

    # second write should keep history bounded
    card2 = {"meta": {"snapshot_ts": "2026-01-01T00:00:01+00:00"}}
    out3 = reporter.persist_health_card(mem, card2, keep_max=1, write_baseline=False, sealed=False)
    hist_files = list((mem / "health_card_history").glob("*.json"))
    assert len(hist_files) <= 1
    assert out3["baseline_written"] is False

    # signature format
    sig_text = Path(out2["baseline_signature"]).read_text(encoding="utf-8").strip()
    assert len(sig_text) == 64
    int(sig_text, 16)


def test_diff_health_card_detects_critical_changes() -> None:
    prev = {
        "files": {"memory_db": {"exists": True, "size_bytes": 100, "sha256": "a" * 64}},
        "counts": {"auto_memory_entries": 100},
        "services": {"launchd_mcp": "running"},
        "environment": {"disk_free_gb": 10.0},
        "runtime": {"shadow_sealed": False},
    }
    cur = {
        "files": {"memory_db": {"exists": False, "size_bytes": 20, "sha256": "b" * 64}},
        "counts": {"auto_memory_entries": 10},
        "services": {"launchd_mcp": "stopped"},
        "environment": {"disk_free_gb": 0.5},
        "runtime": {"shadow_sealed": True},
    }
    out = reporter._diff_health_card(prev, cur)
    assert out["summary"]["critical"] >= 1
    assert any(d["field"] == "files.memory_db.exists" for d in out["diffs"])


def test_health_card_helpers_branches(monkeypatch, tmp_path: Path) -> None:
    # _sqlite_count table guard + missing file
    assert reporter.build_health_card  # import smoke
    assert reporter._diff_health_card({}, {})["summary"]["total"] >= 1

    # _load_repair_summary via persist_report path: create malformed latest
    reports = tmp_path / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "repair_latest.json").write_text("{bad", encoding="utf-8")
    out = reporter._load_repair_summary(tmp_path)
    assert out["status"] in {"missing", "error", "ok"}

    # _emit_macos_notifications non-macos branch
    monkeypatch.setattr(reporter.sys, "platform", "linux")
    emit = reporter._emit_macos_notifications({"emitted": [{"check_id": "x", "status": "warn"}]})
    assert emit["status"] == "skipped"
