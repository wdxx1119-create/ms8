from __future__ import annotations

import json
from pathlib import Path

from ms8.engine_core.knowledge_feedback import KnowledgeFeedbackRecorder


def _config(tmp_path: Path, *, bridge_enabled: bool = True) -> dict:
    return {
        "workspace_dir": tmp_path,
        "settings": {
            "memory": {
                "knowledge_control": {
                    "feedback_log_file": "memory/knowledge_feedback.jsonl",
                    "bridge_app_feedback": {
                        "enabled": bridge_enabled,
                        "store_path": "memory/auto_memory_feedback.jsonl",
                    },
                    "feedback_rebalance": {
                        "output_file": "memory/knowledge_feedback_rebalanced.jsonl",
                        "recent_window": 30,
                        "effective_thresholds": {
                            "hard_trust_min": 0.78,
                            "soft_trust_min": 0.55,
                            "hypothesis_min": 0.28,
                        },
                        "enabled_distribution_shaping": True,
                        "hard_top_ratio": 0.25,
                        "hypothesis_bottom_ratio": 0.25,
                        "min_hard_count": 1,
                        "min_hypothesis_count": 1,
                        "hypothesis_max_score": 0.7,
                    },
                }
            }
        },
    }


def test_record_usage_and_admission_with_bridge(tmp_path: Path) -> None:
    rec = KnowledgeFeedbackRecorder(_config(tmp_path, bridge_enabled=True))
    rec.record_usage(
        "m-1",
        "graph",
        "soft_trust",
        retrieval_hits=3,
        retrieval_waste=1,
        used_in_answer=True,
        extra={"confidence": 0.66},
    )
    rec.record_admission(
        "m-2",
        "core",
        "hard_trust",
        accepted_or_rejected="accepted",
        event="admission",
        extra={"promotion_events": 2, "demotion_events": 0, "confidence": 0.9},
    )

    rows = [json.loads(x) for x in rec.file.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(rows) == 2
    assert rows[0]["event"] == "usage"
    assert rows[1]["event"] == "admission"
    assert "timestamp" in rows[0] and "timestamp" in rows[1]

    bridge_rows = [json.loads(x) for x in rec.bridge_file.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(bridge_rows) == 2
    assert bridge_rows[0]["signal"] == "usage"
    assert bridge_rows[0]["helpful"] is True
    assert bridge_rows[1]["signal"] == "admission"
    assert bridge_rows[1]["helpful"] is True


def test_bridge_disabled_and_raw_rows_tolerate_invalid_json(tmp_path: Path) -> None:
    rec = KnowledgeFeedbackRecorder(_config(tmp_path, bridge_enabled=False))
    rec.record_usage("m-3", "observation", "hypothesis", 0, 1, False, extra={"confidence": "bad"})
    rec.file.write_text(rec.file.read_text(encoding="utf-8") + "{not-json}\n", encoding="utf-8")
    rows = rec._raw_rows()
    assert len(rows) == 1
    assert rows[0]["knowledge_id"] == "m-3"
    # bridge file stays empty when bridge is disabled
    assert rec.bridge_file.read_text(encoding="utf-8") == ""


def test_effective_level_thresholds_and_to_float_fallback(tmp_path: Path) -> None:
    rec = KnowledgeFeedbackRecorder(_config(tmp_path))
    hard = rec._effective_level(
        {
            "trust": "hard_trust",
            "used_in_answer": True,
            "accepted_or_rejected": "accepted",
            "retrieval_hits": 3,
            "retrieval_waste": 0,
            "confidence": 0.95,
        }
    )
    soft = rec._effective_level(
        {
            "trust": "soft_trust",
            "used_in_answer": False,
            "accepted_or_rejected": "rejected",
            "retrieval_hits": 2,
            "retrieval_waste": 0,
            "confidence": 0.6,
        }
    )
    hypo = rec._effective_level(
        {
            "trust": "hypothesis",
            "used_in_answer": False,
            "accepted_or_rejected": "rejected",
            "retrieval_hits": 1,
            "retrieval_waste": 1,
            "confidence": 0.35,
        }
    )
    isolated = rec._effective_level(
        {
            "trust": "isolated",
            "used_in_answer": False,
            "accepted_or_rejected": "rejected",
            "retrieval_hits": 0,
            "retrieval_waste": 3,
            "confidence": "bad-number",
        }
    )
    assert hard["effective_trust"] == "hard_trust"
    assert soft["effective_trust"] in {"soft_trust", "hard_trust"}
    assert hypo["effective_trust"] in {"hypothesis", "soft_trust"}
    assert isolated["effective_trust"] == "isolated"
    assert KnowledgeFeedbackRecorder._to_float("x", 1.23) == 1.23


def test_rebuild_balanced_feedback_empty_and_success(tmp_path: Path) -> None:
    rec = KnowledgeFeedbackRecorder(_config(tmp_path))
    empty = rec.rebuild_balanced_feedback(window=20)
    assert empty["status"] == "empty"
    assert empty["rebalanced"] == 0

    # Build a spread so distribution shaping promotion/demotion can occur.
    for i in range(8):
        trust = "soft_trust" if i < 5 else "hard_trust"
        rec.record_usage(
            f"id-{i}",
            "graph",
            trust,
            retrieval_hits=2 if i < 4 else 0,
            retrieval_waste=0 if i < 4 else 2,
            used_in_answer=i % 2 == 0,
            extra={"confidence": 0.62 if i < 5 else 0.88},
        )
    out = rec.rebuild_balanced_feedback(window=50)
    assert out["status"] == "success"
    assert out["rebalanced"] == 8
    assert out["effective_tier_distribution"]
    lines = [json.loads(x) for x in rec.rebalance_file.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(lines) == 8
    assert all("rebalance_reason" in row for row in lines)
    assert all("effective_score" in row for row in lines)


def test_weekly_threshold_suggestions_branches(tmp_path: Path) -> None:
    rec = KnowledgeFeedbackRecorder(_config(tmp_path))
    assert rec.build_weekly_threshold_suggestion(window=30)["status"] == "empty"

    # Keep usage low and waste high; keep hard low and hypothesis high to trigger all suggestions.
    for i in range(30):
        trust = "hypothesis" if i < 15 else "soft_trust"
        rec.record_usage(
            f"s-{i}",
            "observation",
            trust,
            retrieval_hits=0,
            retrieval_waste=2,
            used_in_answer=False,
            extra={},
        )
    report = rec.build_weekly_threshold_suggestion(window=30)
    assert report["status"] == "success"
    assert report["stats"]["recent_count"] == 30
    keys = {s["key"] for s in report["suggestions"]}
    assert "working_memory.dynamic_injection_budget.simple_top_k" in keys
    assert "working_memory.dynamic_injection_budget.low_trust_ratio_cap" in keys
    assert "knowledge_control.retrieval_mix_balancer.hard_top_ratio" in keys
    assert "knowledge_control.retrieval_mix_balancer.hypothesis_bottom_ratio" in keys
