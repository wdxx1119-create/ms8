from __future__ import annotations

import json
from pathlib import Path

from ms8.engine_core.working_memory import WorkingMemoryManager


def _cfg(tmp_path: Path) -> dict:
    return {
        "workspace_dir": tmp_path,
        "settings": {
            "memory": {
                "working_memory": {
                    "enabled": True,
                    "max_restore": 8,
                    "injection_top_k": 3,
                    "max_injection_chars": 220,
                    "ranking_weights": {"score": 0.4, "recency": 0.3, "confidence": 0.2, "overlap": 0.1},
                    "recency_scoring": {
                        "missing_score": 0.11,
                        "invalid_score": 0.12,
                        "within_1d_score": 1.0,
                        "within_7d_score": 0.8,
                        "within_30d_score": 0.6,
                        "within_90d_score": 0.4,
                        "older_score": 0.2,
                    },
                    "test_filter": {"enabled": True, "keywords": ["test_only", "verification interaction"]},
                }
            }
        },
    }


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rows.append(json.loads(line))
    return rows


def test_append_restore_and_topic_lookup(tmp_path: Path) -> None:
    wm = WorkingMemoryManager(_cfg(tmp_path))
    wm.append_item("alpha build done", 0.8, "release")
    wm.append_item("test_only this should be filtered", 0.9, "noise")
    wm.append_item("beta patch", 0.5, "release")

    restored = wm.restore_items(max_size=5)
    assert restored == ["alpha build done", "beta patch"]

    hits = wm.restore_by_topic("release alpha", limit=5)
    assert hits
    assert hits[0]["topic"] == "release"
    assert all("score" in row for row in hits)


def test_purge_test_rows_keeps_invalid_json_line(tmp_path: Path) -> None:
    wm = WorkingMemoryManager(_cfg(tmp_path))
    wm.persistence_file.write_text(
        "\n".join(
            [
                '{"content":"verification interaction keep?","topic":"x","source":"interaction"}',
                "{bad-json",
                '{"content":"normal row","topic":"y","source":"interaction"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    out = wm.purge_test_rows()
    assert out["status"] == "success"
    assert out["removed"] == 1
    content = wm.persistence_file.read_text(encoding="utf-8")
    assert "{bad-json" in content
    assert "normal row" in content


def test_rank_build_injection_and_usage_log(tmp_path: Path) -> None:
    wm = WorkingMemoryManager(_cfg(tmp_path))
    ranked = wm.rank_search_results(
        query="release plan",
        results=[
            {"content": "release plan finalized", "date": "2030-01-01", "score": 0.2, "confidence": 0.9, "source": "a"},
            {"content": "legacy notes", "date": "", "score": 0.9, "confidence": 0.1, "source": "b"},
            {"content": "release checklist", "date": "invalid-date", "score": 0.3, "source": "c"},
        ],
        top_k=2,
    )
    assert len(ranked) == 2
    assert ranked[0]["working_rank"] >= ranked[1]["working_rank"]
    assert all("recency_score" in x and "overlap" in x for x in ranked)

    injected = wm.build_injection_text(ranked, heading="Injected", max_chars=90)
    assert injected.startswith("## Injected")
    assert injected.count("\n") >= 1

    wm.log_usage("release plan", ranked, channel="response_context", reason="", candidates_count=5, topic_hits_count=2)
    usage = _read_jsonl(wm.usage_log_file)
    assert usage
    row = usage[-1]
    assert row["count"] == len(ranked)
    assert row["used_in_response"] is True
    assert row["reason"] == "injected"


def test_recency_score_branches(tmp_path: Path) -> None:
    wm = WorkingMemoryManager(_cfg(tmp_path))
    now = wm._utc_now()
    assert wm._recency_score("", now) == 0.11
    assert wm._recency_score("not-a-date", now) == 0.12
    assert wm._recency_score("2020-01-01", now) == 0.2
    assert wm._recency_score("2020-01-01T00:00:00Z", now) == 0.2


def test_disabled_mode_short_circuits(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    cfg["settings"]["memory"]["working_memory"]["enabled"] = False
    wm = WorkingMemoryManager(cfg)
    wm.append_item("x", 0.4, "t")
    assert wm.restore_items(3) == []
    wm.log_usage("q", [], reason="none")
    assert wm.persistence_file.read_text(encoding="utf-8") == ""
    assert wm.usage_log_file.read_text(encoding="utf-8") == ""
