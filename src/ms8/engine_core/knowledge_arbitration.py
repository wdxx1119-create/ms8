"""Knowledge arbitration layer between candidate generation and admission."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .knowledge_rules import merge_control_cfg, safe_usage_permission


class KnowledgeArbitrator:
    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

    def __init__(self, config: dict[str, Any]) -> None:
        raw = config["settings"]["memory"].get("knowledge_control", {})
        self.cfg = merge_control_cfg(raw)

    def arbitrate_candidate(self, candidate: dict[str, Any]) -> dict[str, Any]:
        score = float(candidate.get("scores", {}).get("total", 0.0) or 0.0)
        source_type = str(candidate.get("source_type", "synthetic"))
        if source_type == "pattern":
            trust = "hypothesis"
            usage = safe_usage_permission(self.cfg, trust)
            usage["inject"] = "weak"
            usage["speak"] = "hint"
            return {
                "knowledge_tier": "observation",
                "trust_level": trust,
                "admission_decision": "hold",
                "usage_permission": usage,
                "promotion_state": "seeded",
                "arbitrated_at": self._utc_now().isoformat(),
            }

        th = self.cfg.get("candidate_thresholds", {})
        if score >= float(th.get("core_min", 0.92)) and source_type in {"synthetic", "graph"}:
            tier = "core"
            decision = "admit"
            promotion = "promotable"
        elif score >= float(th.get("graph_min", 0.82)):
            tier = "graph"
            decision = "admit"
            promotion = "watch"
        elif score >= float(th.get("short_term_min", 0.68)):
            tier = "short_term"
            decision = "hold"
            promotion = "watch"
        elif score >= 0.5:
            tier = "observation"
            decision = "hold"
            promotion = "seeded"
        else:
            tier = "rejected"
            decision = "reject"
            promotion = "demoted"

        trust = str(self.cfg.get("trust_by_tier", {}).get(tier, "isolated"))
        usage = safe_usage_permission(self.cfg, trust)
        return {
            "knowledge_tier": tier,
            "trust_level": trust,
            "admission_decision": decision,
            "usage_permission": usage,
            "promotion_state": promotion,
            "arbitrated_at": self._utc_now().isoformat(),
        }

    def arbitrate_retrieval(self, item: dict[str, Any]) -> dict[str, Any]:
        trust_score = float(item.get("scores", {}).get("trust", 0.0) or 0.0)
        fusion_score = float(item.get("scores", {}).get("fusion", 0.0) or 0.0)
        search_type = str((item.get("signals", {}) or {}).get("search_type", ""))
        gov = (item.get("raw", {}) or {}).get("governance", {}) or {}
        stale = bool(gov.get("stale", False))
        dup_mentions = int(gov.get("duplicate_mentions", 0) or 0)

        calib = self.cfg.get("retrieval_calibration", {})
        adjusted = trust_score
        if search_type.startswith("hybrid_graph"):
            adjusted += float(calib.get("hybrid_graph_bonus", -0.04))
        elif search_type.startswith("hybrid"):
            adjusted += float(calib.get("hybrid_bonus", 0.03))
        elif search_type == "lexical":
            adjusted += float(calib.get("lexical_bonus", 0.0))
        if stale:
            adjusted += float(calib.get("stale_penalty", -0.08))
        if dup_mentions >= int(calib.get("duplicate_mentions_trigger", 3)):
            adjusted += float(calib.get("duplicate_penalty", -0.06))
        if fusion_score >= float(calib.get("fusion_hard_boost_min", 1.15)):
            adjusted += float(calib.get("fusion_hard_boost", 0.08))
        adjusted = max(0.0, min(1.0, adjusted))

        th = self.cfg.get("retrieval_trust_thresholds", {})
        if adjusted >= float(th.get("hard_trust_min", 0.78)):
            trust = "hard_trust"
            tier = "core"
        elif adjusted >= float(th.get("soft_trust_min", 0.55)):
            trust = "soft_trust"
            tier = "graph"
        elif adjusted >= float(th.get("hypothesis_min", 0.28)):
            trust = "hypothesis"
            tier = "observation"
        else:
            trust = "isolated"
            tier = "rejected"
        usage = safe_usage_permission(self.cfg, trust)
        return {
            "knowledge_tier": tier,
            "trust_level": trust,
            "usage_permission": usage,
            "trust_score_adjusted": adjusted,
        }
