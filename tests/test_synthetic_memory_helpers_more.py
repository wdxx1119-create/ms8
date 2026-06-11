from __future__ import annotations

import tempfile
from pathlib import Path

from ms8.engine_core.synthetic_memory import MemorySynthesizer


class _FakeGraph:
    def list_relations(self, limit=20):
        return [
            {
                "id": 1,
                "relation_type": "uses",
                "subject_name": "A",
                "object_name": "B",
                "strength": 0.9,
                "confidence": 0.8,
                "relation_status": "stable",
                "soft_isolated": False,
            }
        ]

    def search_entities(self, name, limit=1):
        return [{"importance": 0.7}]

    def gap_report(self, min_importance=0.6, max_relations=1, limit=10):
        return [{"canonical_name": "X"}]

    def stats(self):
        return {"entity_total": 10}


class _FakeCore:
    def __init__(self, workspace: Path):
        memory_dir = workspace / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        self.config = {
            "memory_dir": memory_dir,
            "workspace_dir": workspace,
            "settings": {
                "memory": {
                    "synthetic_memory": {
                        "enabled": True,
                        "max_candidates": 10,
                        "min_relation_strength": 0.5,
                        "allowed_relations": ["uses", "depends_on", "related_to"],
                        "quality_thresholds": {
                            "confidence": 0.7,
                            "consistency": 0.8,
                            "novelty": 0.5,
                            "usefulness": 0.6,
                        },
                        "accept_threshold": 0.82,
                        "review_threshold": 0.68,
                        "special_reasoning_enabled": True,
                        "reasoning_only_mode": False,
                        "auto_confirm_high_risk_categories": ["security"],
                        "auto_confirm_high_risk_keywords": ["password", "token"],
                        "two_hop_enabled": True,
                        "pattern_promotion_support_min": 2,
                    }
                }
            },
        }
        self.knowledge_graph = _FakeGraph()
        self.file_store = type("FS", (), {"read_memory_md": lambda self: ""})()

    def remember(self, *args, **kwargs):
        return {"status": "success"}

    def reindex_memory(self):
        return {"ok": True}


class _FakeFeedback:
    def __init__(self) -> None:
        self.events = []

    def record_admission(self, **kwargs):
        self.events.append(kwargs)


def _mk() -> MemorySynthesizer:
    tmp = tempfile.TemporaryDirectory()
    ws = Path(tmp.name)
    core = _FakeCore(ws)
    syn = MemorySynthesizer(core)
    syn._tmp = tmp  # hold reference
    return syn


def test_normalize_and_struct_key_helpers() -> None:
    syn = _mk()
    assert syn._normalize_text(" Open  Claw!! ") == "open claw"
    assert syn._normalize_entity("open claw") == "openclaw"
    assert syn._normalize_relation("??") == "related_to"
    key = syn._struct_key("Open Claw", "使用", "Py")
    assert "openclaw" in key
    assert "uses" in key or "related_to" in key
    assert syn._candidate_id("a|b|c").startswith("cand_")


def test_score_and_triage_paths() -> None:
    syn = _mk()
    relation = {"subject_name": "A", "object_name": "B", "relation_type": "uses", "strength": 0.8, "confidence": 0.9}
    total, scores, breakdown = syn._score_candidate(relation, "a|uses|b", set(), set())
    assert 0.0 <= total <= 1.0
    assert "total" in scores and "usefulness" in breakdown

    assert syn._triage_status(0.95, {"inference_depth": 1}) == "accepted"
    assert syn._triage_status(0.70, {"inference_depth": 1}) == "review"
    assert syn._triage_status(0.20, {"inference_depth": 1}) == "rejected"

    syn.reasoning_only_mode = True
    assert syn._triage_status(0.70, {"inference_depth": 1}) == "candidate_reasoning_only"


def test_two_hop_and_pattern_route() -> None:
    syn = _mk()
    base = [
        {"id": 1, "subject_name": "A", "object_name": "B", "relation_type": "使用", "strength": 0.8, "confidence": 0.9},
        {
            "id": 2,
            "subject_name": "B",
            "object_name": "C",
            "relation_type": "依赖",
            "strength": 0.7,
            "confidence": 0.8,
        },
    ]
    inferred = syn._two_hop_relations(base, limit=5)
    assert inferred
    assert inferred[0]["inference_depth"] == 2

    pstate = {"patterns": {}}
    route = syn._detect_pattern_route(
        {
            "id": 3,
            "subject_name": "Config",
            "object_name": "Decision",
            "relation_type": "使用",
            "description": "config decision pipeline",
            "inference_depth": 1,
        },
        base,
        pstate,
    )
    assert route
    assert "pattern_id" in route


def test_dedupe_state_and_health_report() -> None:
    syn = _mk()
    state = syn._load_state()
    state["candidates"] = [
        {"candidate_id": "1", "struct_key": "a|uses|b", "status": "review", "scores": {"total": 0.6}},
        {"candidate_id": "2", "struct_key": "a|uses|b", "status": "accepted", "scores": {"total": 0.9}},
    ]
    out = syn._dedupe_state_candidates(state)
    assert out["deduped"] >= 1
    assert len(state["candidates"]) == 1

    # persist for health_report and acceptance growth path
    state["candidates"][0]["accepted_at"] = "2099-01-01T00:00:00+00:00"
    syn._save_state(state)
    report = syn.health_report()
    assert report["total_candidates"] >= 1
    assert "duplicate_rate" in report


def test_rollback_preview_and_apply_when_empty() -> None:
    syn = _mk()
    preview = syn.preview_rollback_auto_approved(since_hours=1)
    assert preview["status"] == "success"
    result = syn.rollback_auto_approved(since_hours=1)
    assert result["status"] == "success"
    assert result["rolled_back"] == 0


def test_iter_window_and_rollback_marks_revoked() -> None:
    syn = _mk()
    state = syn._load_state()
    state["candidates"] = [
        {
            "candidate_id": "c1",
            "status": "accepted",
            "auto_approved": True,
            "auto_approved_at": "2099-01-01T00:00:00+00:00",
            "usage_permission": {"recall": True},
        }
    ]
    syn._save_state(state)
    out = syn.rollback_auto_approved(since_hours=1_000_000)
    assert out["rolled_back"] == 1
    state2 = syn._load_state()
    assert state2["candidates"][0]["status"] == "revoked"


def test_classify_risk_and_auto_confirmation_tiers() -> None:
    syn = _mk()
    high, cat = syn._classify_risk("contradicts", "there is security risk")
    assert high is True and cat == "security"
    high2, cat2 = syn._classify_risk("uses", "contains token in statement")
    assert high2 is True and cat2 in {"permission", "security"}
    high3, cat3 = syn._classify_risk("uses", "normal text")
    assert high3 is False and cat3 == "low_risk"

    out1 = syn._triage_auto_confirmation({"confidence": 0.95, "statement": "safe", "relation_type": "uses"})
    assert out1["action"] == "auto_accept"
    out2 = syn._triage_auto_confirmation({"confidence": 0.8, "statement": "safe", "relation_type": "uses"})
    assert out2["tier"] == "medium_conf_review"
    out3 = syn._triage_auto_confirmation({"confidence": 0.2, "statement": "safe", "relation_type": "uses"})
    assert out3["reason"] == "low_confidence_manual_review"


def test_rebalance_review_queue_accept_reject_trim() -> None:
    syn = _mk()
    syn.settings["review_queue_target"] = 1
    state = syn._load_state()
    state["candidates"] = [
        {"candidate_id": "h1", "status": "review", "scores": {"total": 0.95}, "struct_key": "a|uses|b"},
        {"candidate_id": "m1", "status": "review", "scores": {"total": 0.7}, "struct_key": "b|uses|c"},
        {"candidate_id": "m2", "status": "review", "scores": {"total": 0.69}, "struct_key": "c|uses|d"},
        {"candidate_id": "l1", "status": "review", "scores": {"total": 0.1}, "struct_key": "d|uses|e"},
    ]
    syn._save_state(state)
    out = syn.rebalance_review_queue(max_auto_accept=1, apply_writeback=False)
    assert out["accepted"] >= 1
    assert out["rejected"] >= 1

    s2 = syn._load_state()
    statuses = {c["candidate_id"]: c["status"] for c in s2["candidates"]}
    assert statuses["h1"] == "accepted"
    assert statuses["l1"] == "rejected"


def test_accept_with_meta_arbitration_reject_and_feedback() -> None:
    syn = _mk()
    syn.feedback = _FakeFeedback()

    # arbitration reject path
    syn.arbitrator = type("A", (), {"arbitrate_candidate": lambda self, _c: {"admission_decision": "reject"}})()
    item = {"candidate_id": "x1", "struct_key": "a|uses|b", "statement": "s", "relation_type": "uses"}
    hist = {"accepted_struct_keys": [], "rejected_struct_keys": []}
    syn._accept_candidate_with_meta(item, hist, auto_approved=False, policy="")
    assert item["status"] == "rejected"

    # normal accept + auto audit path
    syn.arbitrator = None
    syn._arbitrate_candidate = lambda _c: {
        "knowledge_tier": "observation",
        "trust_level": "hypothesis",
        "admission_decision": "admit",
        "usage_permission": {"recall": True, "inject": "weak", "speak": "hint"},
        "promotion_state": "seeded",
    }
    item2 = {
        "candidate_id": "x2",
        "struct_key": "c|uses|d",
        "statement": "hello",
        "relation_type": "uses",
        "confidence": 0.95,
    }
    syn._accept_candidate_with_meta(item2, hist, auto_approved=True, policy="v1")
    assert item2["status"] == "accepted"
    assert item2["auto_approved"] is True
    assert syn.feedback.events


def test_confirm_and_reject_candidates_filters_and_updates() -> None:
    syn = _mk()
    syn._accept_candidate = lambda item, history: item.update({"status": "accepted"})
    state = syn._load_state()
    state["candidates"] = [
        {"candidate_id": "r1", "status": "review", "scores": {"total": 0.9}},
        {"candidate_id": "r2", "status": "review", "scores": {"total": 0.4}},
        {"candidate_id": "a1", "status": "accepted", "scores": {"total": 0.99}},
    ]
    syn._save_state(state)

    out = syn.confirm_candidates(candidate_ids=["r1", "r2"], min_score=0.8)
    assert len(out["accepted"]) == 1
    assert out["accepted"][0]["candidate_id"] == "r1"

    out2 = syn.reject_candidates(["r2", "a1"])
    assert "r2" in out2["rejected"]
    assert "a1" not in out2["rejected"]


def test_review_candidates_accept_reject_and_skip_finalized() -> None:
    syn = _mk()
    syn._accept_candidate = lambda item, history: item.update({"status": "accepted"})
    state = syn._load_state()
    state["candidates"] = [
        {"candidate_id": "c1", "status": "review", "scores": {"total": 0.8}},
        {"candidate_id": "c2", "status": "review", "scores": {"total": 0.5}},
        {"candidate_id": "c3", "status": "accepted", "scores": {"total": 0.9}},
    ]
    syn._save_state(state)
    res = syn.review_candidates(
        [
            {"candidate_id": "c1", "decision": "accept", "note": "ok"},
            {"candidate_id": "c2", "decision": "reject", "note": "bad"},
            {"candidate_id": "c3", "decision": "reject", "note": "ignored"},
        ]
    )
    assert res["updated"] == 2
    assert res["accepted"] == 1
    assert res["rejected"] == 1


def test_record_candidate_hits_reasoning_and_rebuttal_paths() -> None:
    syn = _mk()
    syn.reasoning_only_mode = True
    syn.rebuttal_reject_threshold = 2
    state = syn._load_state()
    state["candidates"] = [
        {
            "candidate_id": "p1",
            "status": "candidate_reasoning_only",
            "scores": {"total": 0.9},
            "hit_count": 1,
            "rebuttal_count": 0,
            "knowledge_tier": "observation",
            "trust_level": "hypothesis",
        },
        {
            "candidate_id": "p2",
            "status": "review",
            "scores": {"total": 0.4},
            "hit_count": 0,
            "rebuttal_count": 1,
            "knowledge_tier": "observation",
            "trust_level": "hypothesis",
        },
    ]
    syn._save_state(state)
    out = syn.record_candidate_hits(["p1", "p2"], used=True, rebuttal=True)
    assert out["updated"] >= 2
    assert out["promotion_ready"] >= 1
    assert out["rejected"] >= 1


def test_list_candidates_reasoning_alias_and_sort() -> None:
    syn = _mk()
    syn.reasoning_only_mode = True
    state = syn._load_state()
    state["candidates"] = [
        {
            "candidate_id": "h1",
            "status": "candidate_reasoning_only",
            "usage_permission": {"inject": "strong", "speak": "assert"},
            "scores": {"total": 0.8},
            "confidence": 0.8,
            "review_note": "n1",
        },
        {
            "candidate_id": "h2",
            "status": "candidate_reasoning_only",
            "usage_permission": {"inject": "weak", "speak": "hint"},
            "scores": {"total": 0.6},
            "confidence": 0.6,
            "review_note": "n2",
        },
    ]
    syn._save_state(state)
    rows = syn.list_candidates(status="review", limit=10)
    assert len(rows) == 2
    assert rows[0]["candidate_id"] == "h1"
