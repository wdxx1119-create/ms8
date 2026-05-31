from __future__ import annotations

from ms8.engine_core import context_material as cm


def test_tokenize_and_scores():
    assert cm._tokenize("Hello 世界 test-1")[:3]
    assert cm._safe_float("1.5") == 1.5
    assert cm._safe_float(None, 0.3) == 0.3
    assert cm._density_score("") == 0.0
    assert cm._density_score("a:b") > 0
    assert cm._query_coverage(["a"], "a b") > 0
    assert cm._query_coverage([], "a") == 0.0


def test_recency_score_parsing():
    assert cm._recency_score("") == 0.4
    assert 0.2 <= cm._recency_score("2000-01-01") <= 0.5
    assert cm._recency_score("invalid-date") == 0.4
    assert cm._recency_score("2026-05-21 10:00:00") >= 0.25
    assert cm._recency_score("2026-05-21T10:00:00+00:00") >= 0.25


def test_relation_tags_and_topic_match():
    cand = {
        "content": "http://example.com ```code```",
        "source": "daily_log:2026-01-01.md",
        "knowledge_tier": "core",
    }
    tags = cm._relation_tags(cand)
    assert "has_link" in tags
    assert "has_code" in tags
    assert "daily_log" in tags
    assert "tier:core" in tags

    q = ["ms8", "memory"]
    hits = [{"topic": "ms8 memory", "content": "governance"}]
    score = cm._topic_match(q, hits, "ms8 context")
    assert 0.0 <= score <= 1.0


def test_build_candidate_profiles_and_batch():
    candidates = [
        {
            "id": "a1",
            "content": "ms8 memory governance",
            "source": "MEMORY.md",
            "usage_permission": {"inject": "primary"},
            "trust_level": "hard_trust",
            "knowledge_tier": "core",
            "date": "2026-05-20",
        },
        {
            "id": "a2",
            "content": "draft hypothesis",
            "source": "daily_log:2026-05-01.md",
            "usage_permission": {"inject": "none"},
            "trust_level": "hypothesis",
            "knowledge_tier": "observation",
            "date": "2026-03-01",
        },
    ]
    profiles, batch = cm.build_candidate_profiles(
        query="ms8 memory",
        candidates=candidates,
        topic_hits=[{"topic": "memory"}],
        context_signals={"intent_type": "question", "cross_turn_dependency": True},
    )
    assert len(profiles) == 2
    assert batch["candidate_count"] == 2
    assert batch["blocked_count"] == 1
    assert any(p["blocked_reason"] == "usage_permission_blocked" for p in profiles)


def test_query_complexity_and_topic_state():
    c1 = cm._query_complexity("如何设计对比方案，分析根因并优化")
    assert c1["level"] in {"simple", "complex"}
    state = {
        "topic_hit_count": 0,
        "recent_topic_consistency": 0.0,
        "context_assist": {"cross_turn_dependency": False},
    }
    profiles = [{"topic_match": 0.0, "query_coverage": 0.0, "injectability": True}]
    out = cm._infer_topic_state("换个话题", profiles, state, {})
    assert out in {"hard_switch", "shift", "continue"}


def test_dynamic_budget_and_projection_chain():
    profiles = [
        {"injectability": True, "trust": "hard_trust", "source": "MEMORY.md", "id": "h1", "query_coverage": 0.8, "density_score": 0.8, "recency_score": 1.0},
        {"injectability": True, "trust": "hypothesis", "source": "daily_log:1", "id": "l1", "query_coverage": 0.2, "density_score": 0.3, "recency_score": 0.4},
        {"injectability": False, "trust": "soft_trust", "source": "daily_log:2", "id": "x1", "blocked_reason": "usage_permission_blocked", "query_coverage": 0.1, "density_score": 0.2, "recency_score": 0.2},
    ]
    budget = cm.compute_dynamic_injection_budget(
        query="分析这个方案的权衡和冲突",
        profiles=profiles,
        base_top_k=4,
        base_max_chars=1200,
        state={"topic_hit_count": 2, "recent_topic_consistency": 0.4, "context_assist": {"cross_turn_dependency": True}},
        cfg={},
    )
    assert budget["budget_top_k"] >= 1
    material = cm.assemble_shared_context_material(
        text="请分析 https://x.com file.py",
        latest_memories=[{"category": "decision", "tags": ["a", "b"]}],
        retrieval_candidates=[{"source": "MEMORY.md"}],
        topic_hits=[{"topic": "ms8"}],
        state={"topic_state": "continue"},
        candidate_profiles=profiles,
        batch_profile={"avg_query_coverage": 0.5, "tier_distribution": {"core": 1}, "trust_distribution": {"hard_trust": 1}},
        budget=budget,
    )
    cctx = cm.project_classification_context(material)
    rctx = cm.project_response_context(material)
    ictx = cm.project_injection_context(material)
    actx = cm.project_arbitration_context(material)
    assert cctx["query_type"] in {"direct", "analysis", "multi_intent"}
    assert "high_value_candidates" in rctx
    assert "blocked_reasons" in ictx
    assert "source_diversity" in actx

