from __future__ import annotations

import collections
from datetime import datetime, timedelta, timezone

from ms8.engine_core.core import MemoryCore


def _core_stub() -> MemoryCore:
    core = MemoryCore.__new__(MemoryCore)
    core._recent_query_tokens = collections.deque(maxlen=24)
    core.context_understanding = None
    core.pattern_recognition = None
    return core


def test_safe_text_for_memory_md_allow_and_reject() -> None:
    core = _core_stub()
    core._evaluate_admission = lambda text, source="memory_md": {
        "should_write_memory_md": True,
        "route": "accepted",
        "reasons": [],
        "normalized_text": f"{text}-n",
    }
    out = core._safe_text_for_memory_md("hello")
    assert out["allowed"] is True
    assert out["text"] == "hello-n"

    core._evaluate_admission = lambda text, source="memory_md": {
        "should_write_memory_md": False,
        "route": "rejected",
        "reasons": ["r1"],
        "normalized_text": text,
    }
    out = core._safe_text_for_memory_md("hello")
    assert out["allowed"] is False
    assert out["route"] == "rejected"
    assert out["text"] == ""


def test_query_tokens_and_topic_consistency_score() -> None:
    core = _core_stub()
    toks = core._query_tokens("我们讨论ms8-memory 与 代码修复")
    # zh bigram + latin token
    assert "ms8-memory" in toks
    assert any(tok for tok in toks if len(tok) == 2)

    core._recent_query_tokens.append(core._query_tokens("ms8 代码 修复"))
    core._recent_query_tokens.append(core._query_tokens("记忆 系统 治理"))
    score = core._recent_topic_consistency_score("继续做代码修复", window=2)
    assert 0.0 <= score <= 1.0


def test_compute_profile_assist_score_and_context_signals() -> None:
    core = _core_stub()
    cfg = {"context_signal_weight_by_query_type": {"default": 0.06}, "context_signal_assist_cap": 0.18}
    profile = {
        "topic_match": 0.8,
        "query_coverage": 0.7,
        "pronoun_resolution_confidence": 0.5,
        "cross_turn_dependency": True,
    }
    score = core._compute_profile_assist_score(profile, "analysis", cfg)
    assert 0.0 <= score <= 0.18

    sig = core._get_context_assist_signals("我们昨天讨论过这个问题，现在继续。")
    assert sig["time_reference"] in {"past", "present"}


def test_policy_action_due_parsing_and_cooldown() -> None:
    core = _core_stub()
    now = datetime.now(timezone.utc)
    core._utc_now = lambda: now

    state = {"last_runs": {"a": (now - timedelta(hours=5)).isoformat()}}
    assert core._policy_action_due(state, "a", cooldown_hours=4) is True
    assert core._policy_action_due(state, "a", cooldown_hours=12) is False

    # invalid timestamp -> fail-open True
    bad = {"last_runs": {"a": "not-a-date"}}
    assert core._policy_action_due(bad, "a", cooldown_hours=4) is True
