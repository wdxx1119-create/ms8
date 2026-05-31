from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from ms8.engine_core import governance


def _patch_config(monkeypatch, tmp_path: Path) -> None:
    cfg = {
        "memory_dir": tmp_path / "memory",
        "settings": {
            "memory": {
                "governance": {
                    "semantic_overlap_threshold": 0.5,
                    "trust_scoring": {
                        "base_score": 0.5,
                        "memory_md_bonus": 0.3,
                        "daily_log_bonus": 0.2,
                        "default_source_bonus": 0.1,
                        "stale_penalty": 0.2,
                    },
                }
            }
        },
    }
    monkeypatch.setattr(governance, "get_config", lambda: cfg)


def test_governance_load_invalid_json_falls_back(monkeypatch, tmp_path: Path) -> None:
    _patch_config(monkeypatch, tmp_path)
    report = (tmp_path / "memory" / "governance_report.json")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("{bad json", encoding="utf-8")
    gov = governance.MemoryGovernance()
    assert gov.state == {"entries": [], "alerts": []}


def test_assess_memory_write_detects_duplicate_conflict_and_semantic(monkeypatch, tmp_path: Path) -> None:
    _patch_config(monkeypatch, tmp_path)
    gov = governance.MemoryGovernance()
    out = gov.assess_memory_write(
        content="I always prefer python tooling for service deploys",
        existing_blocks={
            "block_dup": "i always prefer python tooling for service deploys in prod",
            "block_conflict": "i always prefer python tooling for service deploys but i never use it",
            "block_semantic": "Python tooling for deploy workflows remains common and stable",
        },
    )
    assert out["is_duplicate"] is True
    assert "block_dup" in out["duplicate_blocks"]
    assert out["has_conflict"] is True
    assert "block_conflict" in out["conflict_blocks"]
    assert gov.state["alerts"]


def test_semantic_overlap_empty_tokens(monkeypatch, tmp_path: Path) -> None:
    _patch_config(monkeypatch, tmp_path)
    gov = governance.MemoryGovernance()
    assert gov._semantic_overlap("!!!", "...") == 0.0


def test_assess_memory_write_adds_semantic_conflict(monkeypatch, tmp_path: Path) -> None:
    _patch_config(monkeypatch, tmp_path)
    gov = governance.MemoryGovernance()
    out = gov.assess_memory_write(
        content="Prefer python tooling for service deploys in production",
        existing_blocks={
            "near_match": "Python tooling for production deploys remains stable for services",
        },
    )
    assert out["semantic_conflicts"]
    assert out["semantic_conflicts"][0]["source"] == "near_match"


def test_annotate_search_result_and_trust_score(monkeypatch, tmp_path: Path) -> None:
    _patch_config(monkeypatch, tmp_path)
    gov = governance.MemoryGovernance()
    gov.assess_memory_write(content="Prefer two spaces", existing_blocks={})
    recent = gov.annotate_search_result(
        {
            "content": "Prefer two spaces in yaml formatting",
            "source": "MEMORY.md",
            "date": datetime.now(),
        }
    )
    assert recent["governance"]["stale"] is False
    assert recent["governance"]["trust_score"] > 0.7
    assert recent["governance"]["duplicate_mentions"] >= 1

    stale = gov.annotate_search_result(
        {
            "content": "old item",
            "source": "daily_log:2026-01-01",
            "date": datetime.now() - timedelta(days=90),
        }
    )
    assert stale["governance"]["stale"] is True
    assert 0.0 <= stale["governance"]["trust_score"] <= 1.0


def test_report_respects_limit(monkeypatch, tmp_path: Path) -> None:
    _patch_config(monkeypatch, tmp_path)
    gov = governance.MemoryGovernance()
    for idx in range(5):
        gov.assess_memory_write(content=f"record {idx}", existing_blocks={})
    data = gov.report(limit=2)
    assert len(data["entries"]) == 2
    assert "core_metric_contract" in data
