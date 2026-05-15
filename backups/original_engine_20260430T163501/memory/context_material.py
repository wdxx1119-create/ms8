"""Shared context assembly: build once, then project per use case."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List


HIGH_TRUST_LEVELS = {"hard_trust", "soft_trust"}
LOW_TRUST_LEVELS = {"hypothesis", "isolated"}


def _tokenize(text: str) -> List[str]:
    return [m.group(0).lower() for m in re.finditer(r"[A-Za-z0-9_\-]+|[\u4e00-\u9fff]", text or "")]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _recency_score(raw_date: str) -> float:
    if not raw_date:
        return 0.4
    dt = None
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(str(raw_date), fmt)
            dt = dt.replace(tzinfo=timezone.utc)
            break
        except Exception:
            dt = None
    if dt is None:
        try:
            raw = str(raw_date).strip()
            if raw.endswith("Z"):
                raw = raw[:-1] + "+00:00"
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
        except Exception:
            return 0.4
    age = datetime.now(timezone.utc) - dt
    if age <= timedelta(days=1):
        return 1.0
    if age <= timedelta(days=7):
        return 0.8
    if age <= timedelta(days=30):
        return 0.6
    if age <= timedelta(days=90):
        return 0.45
    return 0.25


def _density_score(content: str) -> float:
    tokens = _tokenize(content)
    if not tokens:
        return 0.0
    uniq = len(set(tokens))
    ratio = uniq / max(1, len(tokens))
    punct = 1 if any(x in content for x in (":", "=", "->", "=>")) else 0
    return round(max(0.0, min(1.0, 0.75 * ratio + 0.25 * punct)), 4)


def _query_coverage(query_tokens: List[str], content: str) -> float:
    if not query_tokens:
        return 0.0
    ct = set(_tokenize(content))
    if not ct:
        return 0.0
    return round(len(set(query_tokens) & ct) / max(1, len(set(query_tokens))), 4)


def _topic_match(query_tokens: List[str], topic_hits: List[Dict[str, Any]], content: str) -> float:
    if not query_tokens:
        return 0.0
    base = _query_coverage(query_tokens, content)
    if not topic_hits:
        return base
    topic_blob = " ".join(str(x.get("topic", "")) + " " + str(x.get("content", "")) for x in topic_hits[:5])
    topic_cov = _query_coverage(query_tokens, topic_blob)
    return round(max(0.0, min(1.0, 0.6 * base + 0.4 * topic_cov)), 4)


def _relation_tags(candidate: Dict[str, Any]) -> List[str]:
    content = str(candidate.get("content", ""))
    source = str(candidate.get("source", ""))
    tags: List[str] = []
    if "http" in content:
        tags.append("has_link")
    if "```" in content or "[CODE_BLOCK]" in content:
        tags.append("has_code")
    if source.startswith("daily_log:"):
        tags.append("daily_log")
    if source == "MEMORY.md":
        tags.append("memory_md")
    tier = str(candidate.get("knowledge_tier", "observation"))
    tags.append(f"tier:{tier}")
    return tags[:8]


def build_candidate_profiles(
    query: str,
    candidates: List[Dict[str, Any]],
    topic_hits: List[Dict[str, Any]] | None = None,
    context_signals: Dict[str, Any] | None = None,
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    topic_hits = topic_hits or []
    context_signals = context_signals or {}
    qt = _tokenize(query)
    profiles: List[Dict[str, Any]] = []
    tier_dist: Dict[str, int] = {}
    trust_dist: Dict[str, int] = {}
    blocked_count = 0
    injectable_count = 0
    coverage_sum = 0.0

    for idx, c in enumerate(candidates):
        usage = c.get("usage_permission", {}) or {}
        inject_mode = str(usage.get("inject", "weak"))
        injectability = inject_mode != "none"
        blocked_reason = ""
        if not injectability:
            blocked_reason = "usage_permission_blocked"
            blocked_count += 1
        if injectability:
            injectable_count += 1
        trust = str(c.get("trust_level", "hypothesis"))
        tier = str(c.get("knowledge_tier", "observation"))
        coverage = _safe_float(c.get("overlap", None), -1.0)
        if coverage < 0:
            coverage = _query_coverage(qt, str(c.get("content", "")))
        recency = _safe_float(c.get("recency_score", None), -1.0)
        if recency < 0:
            recency = _recency_score(str(c.get("date", "")))
        density = _density_score(str(c.get("content", "")))
        tmatch = _topic_match(qt, topic_hits, str(c.get("content", "")))
        cid = str(c.get("id", "")).strip() or f"cand:{idx}"
        profile = {
            "id": cid,
            "source": str(c.get("source", "")),
            "tier": tier,
            "trust": trust,
            "recency_score": round(recency, 4),
            "query_coverage": round(coverage, 4),
            "density_score": density,
            "topic_match": tmatch,
            "injectability": bool(injectability),
            "blocked_reason": blocked_reason,
            "relation_tags": _relation_tags(c),
            "intent_type": str(context_signals.get("intent_type", "statement")),
            "emotional_mode": str(context_signals.get("emotional_mode", "neutral")),
            "time_reference": str(context_signals.get("time_reference", "none")),
            "cross_turn_dependency": bool(context_signals.get("cross_turn_dependency", False)),
            "pronoun_resolution_confidence": _safe_float(context_signals.get("pronoun_resolution_confidence", 0.0), 0.0),
        }
        profiles.append(profile)
        tier_dist[tier] = tier_dist.get(tier, 0) + 1
        trust_dist[trust] = trust_dist.get(trust, 0) + 1
        coverage_sum += coverage

    batch = {
        "candidate_count": len(profiles),
        "injectable_count": injectable_count,
        "blocked_count": blocked_count,
        "tier_distribution": tier_dist,
        "trust_distribution": trust_dist,
        "source_diversity": len({p["source"] for p in profiles if p["source"]}),
        "avg_query_coverage": round(coverage_sum / max(1, len(profiles)), 4),
    }
    return profiles, batch


def _query_complexity(query: str) -> Dict[str, Any]:
    units = re.findall(r"[A-Za-z0-9_\-]+|[\u4e00-\u9fff]{2,}", query or "")
    token_len = len(units)
    cues = ("why", "how", "compare", "tradeoff", "design", "分析", "架构", "对比", "方案", "冲突", "根因", "优化")
    has_complex_cue = any(c in query.lower() for c in cues)
    multi_clause = int(query.count("，") + query.count(",") + query.count("并") + query.count("且")) >= 2
    score = min(1.0, 0.1 * min(6, token_len) + (0.32 if has_complex_cue else 0.0) + (0.2 if multi_clause else 0.0))
    level = "complex" if score >= 0.58 else "simple"
    qtype = "analysis" if has_complex_cue else ("multi_intent" if multi_clause else "direct")
    return {"score": round(score, 4), "level": level, "query_type": qtype, "token_len": token_len}


def _infer_topic_state(
    query: str,
    profiles: List[Dict[str, Any]],
    state: Dict[str, Any],
    cfg: Dict[str, Any],
) -> str:
    if str(state.get("topic_state", "")) in {"continue", "shift", "hard_switch"}:
        return str(state.get("topic_state"))

    hit_count = int(state.get("topic_hit_count", 0) or 0)
    if profiles:
        avg_topic_match = sum(float(p.get("topic_match", 0.0) or 0.0) for p in profiles) / max(1, len(profiles))
        avg_coverage = sum(float(p.get("query_coverage", 0.0) or 0.0) for p in profiles) / max(1, len(profiles))
    else:
        avg_topic_match = 0.0
        avg_coverage = 0.0

    switch_cues = cfg.get(
        "hard_switch_cues",
        ["换个", "新话题", "另一个问题", "不相关", "切换", "by the way", "anyway"],
    )
    has_switch_cue = any(str(cue).lower() in query.lower() for cue in switch_cues)
    assist = state.get("context_assist", {}) if isinstance(state, dict) else {}
    cross_turn_dependency = bool(assist.get("cross_turn_dependency", False))
    recent_consistency = _safe_float(state.get("recent_topic_consistency", 0.0), 0.0)

    continue_min_hits = int(cfg.get("topic_continue_min_hits", 2))
    continue_topic_match = _safe_float(cfg.get("topic_continue_min_match", 0.22), 0.22)
    continue_consistency = _safe_float(cfg.get("topic_continue_min_consistency", 0.34), 0.34)
    hard_switch_max_hits = int(cfg.get("topic_hard_switch_max_hits", 0))
    hard_switch_max_cov = _safe_float(cfg.get("topic_hard_switch_max_coverage", 0.08), 0.08)
    hard_switch_max_consistency = _safe_float(cfg.get("topic_hard_switch_max_consistency", 0.12), 0.12)

    if has_switch_cue:
        if hit_count <= hard_switch_max_hits or avg_coverage <= max(0.2, hard_switch_max_cov):
            return "hard_switch"
        return "shift"
    if (
        hit_count >= continue_min_hits
        or avg_topic_match >= continue_topic_match
        or recent_consistency >= continue_consistency
        or (cross_turn_dependency and recent_consistency >= continue_consistency * 0.8)
    ):
        return "continue"
    if (
        hit_count <= hard_switch_max_hits
        and avg_coverage <= hard_switch_max_cov
        and recent_consistency <= hard_switch_max_consistency
        and not cross_turn_dependency
    ):
        return "hard_switch"
    return "shift"


def compute_dynamic_injection_budget(
    query: str,
    profiles: List[Dict[str, Any]],
    base_top_k: int,
    base_max_chars: int,
    state: Dict[str, Any] | None = None,
    cfg: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    state = state or {}
    cfg = cfg or {}
    q = _query_complexity(query)
    simple_top_k = int(cfg.get("simple_top_k", max(2, min(3, base_top_k))))
    complex_top_k = int(cfg.get("complex_top_k", max(6, base_top_k + 1)))
    simple_chars = int(cfg.get("simple_max_chars", max(600, int(base_max_chars * 0.55))))
    complex_chars = int(cfg.get("complex_max_chars", max(base_max_chars, int(base_max_chars * 1.25))))
    low_trust_ratio_cap = _safe_float(cfg.get("low_trust_ratio_cap", 0.30), 0.30)

    injectable = [p for p in profiles if p.get("injectability", False)]
    high = [p for p in injectable if str(p.get("trust", "")) in HIGH_TRUST_LEVELS]
    low = [p for p in injectable if str(p.get("trust", "")) in LOW_TRUST_LEVELS]
    high_ratio = len(high) / max(1, len(injectable))
    low_ratio = len(low) / max(1, len(injectable))

    if q["level"] == "simple":
        top_k = simple_top_k
        max_chars = simple_chars
    else:
        candidate_boost = 1 if len(injectable) >= 10 else 0
        top_k = min(max(6, complex_top_k + candidate_boost), max(8, base_top_k + 3))
        max_chars = complex_chars

    if high_ratio < 0.5 and top_k > 3:
        top_k = max(3, top_k - 1)
    top_k = min(top_k, max(1, len(injectable))) if injectable else top_k
    low_cap_count = max(1, int(round(top_k * low_trust_ratio_cap)))
    topic_state = _infer_topic_state(query, profiles, state, cfg)
    if topic_state == "continue":
        top_k = min(top_k, max(2, simple_top_k))
        max_chars = min(max_chars, max(simple_chars, int(base_max_chars * 0.65)))
    elif topic_state == "shift":
        top_k = min(max(top_k, simple_top_k + 1), max(8, complex_top_k))
    else:  # hard_switch
        top_k = min(max(3, top_k), max(6, complex_top_k - 1))
        low_cap_count = min(low_cap_count, int(cfg.get("hard_switch_low_trust_cap", 1)))
    return {
        "query_type": q["query_type"],
        "complexity_level": q["level"],
        "complexity_score": q["score"],
        "budget_top_k": int(top_k),
        "budget_chars": int(max_chars),
        "low_trust_cap_count": int(low_cap_count),
        "high_trust_ratio": round(high_ratio, 4),
        "low_trust_ratio": round(low_ratio, 4),
        "topic_state": topic_state,
    }


def assemble_shared_context_material(
    text: str,
    latest_memories: List[Dict[str, Any]] | None = None,
    retrieval_candidates: List[Dict[str, Any]] | None = None,
    topic_hits: List[Dict[str, Any]] | None = None,
    state: Dict[str, Any] | None = None,
    candidate_profiles: List[Dict[str, Any]] | None = None,
    batch_profile: Dict[str, Any] | None = None,
    budget: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    latest_memories = latest_memories or []
    retrieval_candidates = retrieval_candidates or []
    topic_hits = topic_hits or []
    state = state or {}
    candidate_profiles = candidate_profiles or []
    batch_profile = batch_profile or {}
    budget = budget or {}

    links = re.findall(r"https?://\S+", text)
    files = re.findall(r"\b[\w./-]+\.(?:py|js|ts|md|yaml|yml|json|toml|sql|sh)\b", text)

    return {
        "input_text": text,
        "query_tokens": _tokenize(text),
        "has_code": "```" in text or "[CODE_BLOCK]" in text,
        "links": links[:5],
        "files": files[:5],
        "recent_categories": [m.get("category") for m in latest_memories[:5]],
        "recent_tags": sorted({tag for m in latest_memories[:5] for tag in m.get("tags", [])})[:8],
        "recent_memory_count": len(latest_memories),
        "candidate_count": len(retrieval_candidates),
        "candidate_sources": sorted({str(m.get("source", "")) for m in retrieval_candidates if m.get("source")})[:10],
        "topic_hit_count": len(topic_hits),
        "profiles": candidate_profiles,
        "batch_profile": batch_profile,
        "budget": budget,
        "state": state,
    }


def project_classification_context(material: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "has_code": bool(material.get("has_code", False)),
        "links": list(material.get("links", []))[:5],
        "files": list(material.get("files", []))[:5],
        "recent_categories": list(material.get("recent_categories", []))[:5],
        "recent_tags": list(material.get("recent_tags", []))[:8],
        "query_type": str((material.get("budget") or {}).get("query_type", "direct")),
        "complexity_level": str((material.get("budget") or {}).get("complexity_level", "simple")),
    }


def project_response_context(material: Dict[str, Any]) -> Dict[str, Any]:
    profiles = list(material.get("profiles", []))
    high_value = [
        p for p in profiles
        if p.get("injectability", False) and str(p.get("trust", "")) in HIGH_TRUST_LEVELS
    ]
    high_value.sort(key=lambda x: (x.get("query_coverage", 0.0), x.get("density_score", 0.0), x.get("recency_score", 0.0)), reverse=True)
    return {
        "query_tokens": list(material.get("query_tokens", []))[:24],
        "candidate_count": int(material.get("candidate_count", 0)),
        "topic_hit_count": int(material.get("topic_hit_count", 0)),
        "candidate_sources": list(material.get("candidate_sources", []))[:8],
        "query_type": str((material.get("budget") or {}).get("query_type", "direct")),
        "complexity_level": str((material.get("budget") or {}).get("complexity_level", "simple")),
        "high_value_candidates": high_value[:5],
    }


def project_injection_context(material: Dict[str, Any]) -> Dict[str, Any]:
    profiles = list(material.get("profiles", []))
    blocked: Dict[str, int] = {}
    for p in profiles:
        reason = str(p.get("blocked_reason", "")).strip()
        if reason:
            blocked[reason] = blocked.get(reason, 0) + 1
    return {
        "budget": dict(material.get("budget", {})),
        "batch_profile": dict(material.get("batch_profile", {})),
        "blocked_reasons": blocked,
        "trust_quota": {
            "high_target_ratio": 0.7,
            "low_cap_ratio": 0.3,
        },
    }


def project_arbitration_context(material: Dict[str, Any]) -> Dict[str, Any]:
    profiles = list(material.get("profiles", []))
    sources = {str(p.get("source", "")) for p in profiles if p.get("source")}
    by_id: Dict[str, List[str]] = {}
    for p in profiles:
        by_id.setdefault(str(p.get("id", "")), []).append(str(p.get("source", "")))
    multi_source_ids = [k for k, v in by_id.items() if len(set(v)) > 1 and k]
    return {
        "source_diversity": len(sources),
        "multi_source_candidate_ids": multi_source_ids[:20],
        "avg_query_coverage": float((material.get("batch_profile") or {}).get("avg_query_coverage", 0.0)),
        "tier_distribution": dict((material.get("batch_profile") or {}).get("tier_distribution", {})),
        "trust_distribution": dict((material.get("batch_profile") or {}).get("trust_distribution", {})),
    }
