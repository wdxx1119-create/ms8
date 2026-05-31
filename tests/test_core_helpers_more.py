from __future__ import annotations

import collections
from datetime import datetime, timezone
from pathlib import Path

from ms8.engine_core.core import MemoryCore


class _DummyFileStore:
    def __init__(self, text: str) -> None:
        self._text = text

    def read_memory_md(self) -> str:
        return self._text


def _core_stub(tmp_path: Path) -> MemoryCore:
    core = MemoryCore.__new__(MemoryCore)
    core.config = {
        "workspace_dir": tmp_path,
        "memory_dir": tmp_path / "memory",
        "settings": {
            "memory": {
                "working_memory": {
                    "high_importance_keywords": ["risk", "security", "关键"],
                    "importance_estimation": {
                        "base_score": 0.4,
                        "keyword_hit_bonus": 0.1,
                        "long_text_threshold": 10,
                        "long_text_bonus": 0.1,
                        "punctuation_bonus": 0.05,
                    },
                },
                "retrieval_fusion": {
                    "memory_md_fallback_base_score": 0.2,
                    "query_intent_source_prior": {
                        "enabled": True,
                        "governance_keywords": ["policy", "review", "queue"],
                        "prefer_source_prefixes": ["openclaw_session"],
                        "governance_title_boost_keywords": ["policy", "review"],
                        "narrative_penalty_keywords": ["storyline", "fiction"],
                        "recent_days_threshold": 14,
                        "recent_days_bonus": 0.04,
                        "stale_days_threshold": 45,
                        "stale_days_penalty": -0.05,
                        "max_boost": 0.18,
                        "min_penalty": -0.22,
                    },
                },
                "expression_router": {"enabled": True, "profile": {"decay": 0.95}},
                "config_audit": {"enabled": True, "report_file": "memory/config_audit_report.json"},
                "governance": {"trust_scoring": {"enabled": True}},
                "meta_cognition_thresholds": {"enabled": True},
                "knowledge_graph_quality": {"enabled": True},
                "self_improvement_scoring": {"enabled": True},
            }
        },
    }
    core.file_store = _DummyFileStore("# Title\npolicy queue status\nnormal line\n")
    core._recent_query_tokens = collections.deque(maxlen=24)
    return core


def test_core_helper_scoring_and_topic(tmp_path: Path) -> None:
    c = _core_stub(tmp_path)
    score = c._estimate_importance("This is a risk update!")
    assert score >= 0.55
    assert c._infer_topic("the and with 我们 项目 使用Python") in {"项目", "使用Python", "Python"}


def test_memory_md_fallback_and_fingerprints(tmp_path: Path) -> None:
    c = _core_stub(tmp_path)
    hits = c._memory_md_fallback_hits("policy queue", limit=3)
    assert hits
    assert hits[0]["source"] == "MEMORY.md"

    ref = c._build_memory_ref("chat", "My Title", "content")
    assert ref.startswith("chat::my-title::")
    fp1 = c._retrieval_fingerprint("s", "t", "  Same   Content ")
    fp2 = c._retrieval_fingerprint("x", "y", "same content")
    assert fp1 == fp2


def test_query_domain_and_datetime_parse(tmp_path: Path) -> None:
    c = _core_stub(tmp_path)
    assert c._query_domain("please review policy queue") == "governance"
    assert c._query_domain("hello world") == "general"

    dt = c._parse_any_datetime("2026-05-19T00:00:00Z")
    assert isinstance(dt, datetime)
    assert dt.tzinfo is not None
    assert c._parse_any_datetime("bad-value") is None
    now = datetime.now(timezone.utc)
    assert c._parse_any_datetime(now) is not None


def test_tokens_consistency_and_profile_assist(tmp_path: Path) -> None:
    c = _core_stub(tmp_path)
    toks = c._query_tokens("policy 队列 风险")
    assert "policy" in toks
    assert "队列" in toks or any("队" in x for x in toks)

    c._recent_query_tokens.append(c._query_tokens("policy queue security"))
    c._recent_query_tokens.append(c._query_tokens("review queue governance"))
    consistency = c._recent_topic_consistency_score("policy queue now", window=6)
    assert 0.0 <= consistency <= 1.0

    profile = {
        "topic_match": 0.8,
        "query_coverage": 0.7,
        "pronoun_resolution_confidence": 0.5,
        "cross_turn_dependency": True,
    }
    cfg = {
        "context_signal_weight_by_query_type": {"question": 0.1, "default": 0.05},
        "context_signal_assist_cap": 0.2,
    }
    assist = c._compute_profile_assist_score(profile, "question", cfg)
    assert 0.0 <= assist <= 0.2
    assert c._expression_router_config().get("enabled") is True


def test_source_prior_adjust_governance_boost_and_penalty(tmp_path: Path) -> None:
    c = _core_stub(tmp_path)
    c._utc_now = lambda: datetime(2026, 5, 20, tzinfo=timezone.utc)  # type: ignore[method-assign]
    row = {
        "source": "openclaw_session:main",
        "title": "Policy review snapshot",
        "content": "queue status",
        "date": "2026-05-19T00:00:00Z",
    }
    boost, reasons = c._source_prior_adjust(row, "please review policy queue")
    assert boost > 0
    assert "src:openclaw_session" in reasons
    assert "gov_kw" in reasons
    assert "recent_bonus" in reasons

    penalized = {
        "source": "memory.md",
        "title": "storyline notes",
        "content": "fiction narrative",
        "date": "2026-01-01T00:00:00Z",
    }
    boost2, reasons2 = c._source_prior_adjust(penalized, "policy review queue")
    assert boost2 <= 0.06
    assert "narrative_penalty" in reasons2


def test_safe_text_for_memory_md_accepted_and_rejected(tmp_path: Path) -> None:
    c = _core_stub(tmp_path)
    c._evaluate_admission = lambda text, source="memory_md": {  # type: ignore[method-assign]
        "should_write_memory_md": False,
        "route": "rejected",
        "reasons": ["noise"],
        "normalized_text": "",
    }
    blocked = c._safe_text_for_memory_md("x")
    assert blocked["allowed"] is False
    assert blocked["text"] == ""

    c._evaluate_admission = lambda text, source="memory_md": {  # type: ignore[method-assign]
        "should_write_memory_md": True,
        "route": "accepted",
        "reasons": [],
        "normalized_text": " normalized ",
    }
    ok = c._safe_text_for_memory_md("x")
    assert ok["allowed"] is True
    assert ok["text"] == "normalized"


def test_write_config_audit_report_emits_json(tmp_path: Path) -> None:
    c = _core_stub(tmp_path)
    c._write_config_audit_report()
    report = tmp_path / "memory" / "config_audit_report.json"
    assert report.exists()
    txt = report.read_text(encoding="utf-8")
    assert "coverage_ratio" in txt
