"""Lightweight feedback recorder for knowledge usage and admission lifecycle."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class KnowledgeFeedbackRecorder:
    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        kb_cfg = config["settings"]["memory"].get("knowledge_control", {})
        raw = kb_cfg.get("feedback_log_file", "memory/knowledge_feedback.jsonl")
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = config["workspace_dir"] / path
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text("", encoding="utf-8")
        self.file = path
        bridge_cfg = kb_cfg.get("bridge_app_feedback", {})
        self.bridge_enabled = bool(bridge_cfg.get("enabled", True))
        bridge_raw = bridge_cfg.get("store_path", "memory/auto_memory_feedback.jsonl")
        bridge_path = Path(bridge_raw).expanduser()
        if not bridge_path.is_absolute():
            bridge_path = config["workspace_dir"] / bridge_path
        bridge_path.parent.mkdir(parents=True, exist_ok=True)
        if not bridge_path.exists():
            bridge_path.write_text("", encoding="utf-8")
        self.bridge_file = bridge_path
        rb_cfg = kb_cfg.get("feedback_rebalance", {})
        rebalance_raw = rb_cfg.get("output_file", "memory/knowledge_feedback_rebalanced.jsonl")
        rebalance_path = Path(rebalance_raw).expanduser()
        if not rebalance_path.is_absolute():
            rebalance_path = config["workspace_dir"] / rebalance_path
        rebalance_path.parent.mkdir(parents=True, exist_ok=True)
        if not rebalance_path.exists():
            rebalance_path.write_text("", encoding="utf-8")
        self.rebalance_file = rebalance_path
        self.rebalance_cfg = rb_cfg

    def _append(self, payload: dict[str, Any]) -> None:
        payload["timestamp"] = self._utc_now().isoformat()
        with self.file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self._append_bridge_feedback(payload)

    def _append_bridge_feedback(self, payload: dict[str, Any]) -> None:
        """Mirror core knowledge feedback into app feedback schema to avoid split data islands."""
        if not self.bridge_enabled:
            return
        event = str(payload.get("event", "usage"))
        helpful = bool(payload.get("used_in_answer", False))
        if event == "admission":
            helpful = str(payload.get("accepted_or_rejected", "")) == "accepted"
        row = {
            "memory_id": str(payload.get("knowledge_id", "")),
            "signal": event,
            "category": str(payload.get("tier", "observation")),
            "helpful": helpful,
            "note": str(payload.get("reason", "")),
            "source": "core_knowledge_feedback",
            "confidence": float(payload.get("confidence", 0.0) or 0.0),
            "created_at": str(payload.get("timestamp", self._utc_now().isoformat())),
        }
        with self.bridge_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def record_admission(
        self,
        knowledge_id: str,
        tier: str,
        trust: str,
        accepted_or_rejected: str,
        event: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self._append(
            {
                "event": event,
                "knowledge_id": knowledge_id,
                "tier": tier,
                "trust": trust,
                "accepted_or_rejected": accepted_or_rejected,
                "promotion_events": int((extra or {}).get("promotion_events", 0)),
                "demotion_events": int((extra or {}).get("demotion_events", 0)),
                **(extra or {}),
            }
        )

    def record_usage(
        self,
        knowledge_id: str,
        tier: str,
        trust: str,
        retrieval_hits: int,
        retrieval_waste: int,
        used_in_answer: bool,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self._append(
            {
                "event": "usage",
                "knowledge_id": knowledge_id,
                "tier": tier,
                "trust": trust,
                "retrieval_hits": int(retrieval_hits),
                "retrieval_waste": int(retrieval_waste),
                "used_in_answer": bool(used_in_answer),
                **(extra or {}),
            }
        )

    def _raw_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if not self.file.exists():
            return rows
        for line in self.file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows

    @staticmethod
    def _to_float(v: Any, default: float = 0.0) -> float:
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    def _effective_level(self, row: dict[str, Any]) -> dict[str, Any]:
        trust = str(row.get("trust", "hypothesis"))
        str(row.get("tier", "observation"))
        used = bool(row.get("used_in_answer", False))
        accepted = str(row.get("accepted_or_rejected", "")) == "accepted"
        hits = int(self._to_float(row.get("retrieval_hits", 0), 0.0))
        waste = int(self._to_float(row.get("retrieval_waste", 0), 0.0))
        adjusted = self._to_float(row.get("trust_score_adjusted", row.get("confidence", 0.0)), 0.0)

        base_map = {"hard_trust": 0.90, "soft_trust": 0.66, "hypothesis": 0.42, "isolated": 0.12}
        score = max(adjusted, base_map.get(trust, 0.35))
        if used:
            score += 0.08
        if accepted:
            score += 0.06
        if waste > hits:
            score -= 0.10
        score = max(0.0, min(1.0, score))

        th = self.rebalance_cfg.get("effective_thresholds", {})
        hard_min = self._to_float(th.get("hard_trust_min", 0.78), 0.78)
        soft_min = self._to_float(th.get("soft_trust_min", 0.55), 0.55)
        hypo_min = self._to_float(th.get("hypothesis_min", 0.28), 0.28)

        if score >= hard_min:
            return {
                "effective_trust": "hard_trust",
                "effective_tier": "core",
                "effective_score": score,
            }
        if score >= soft_min:
            return {
                "effective_trust": "soft_trust",
                "effective_tier": "graph",
                "effective_score": score,
            }
        if score >= hypo_min:
            return {
                "effective_trust": "hypothesis",
                "effective_tier": "observation",
                "effective_score": score,
            }
        return {
            "effective_trust": "isolated",
            "effective_tier": "rejected",
            "effective_score": score,
        }

    def rebuild_balanced_feedback(self, window: int | None = None) -> dict[str, Any]:
        rows = self._raw_rows()
        total = len(rows)
        if total == 0:
            self.rebalance_file.write_text("", encoding="utf-8")
            return {
                "status": "empty",
                "total": 0,
                "rebalanced": 0,
                "output_file": str(self.rebalance_file),
            }

        win = int(window or self.rebalance_cfg.get("recent_window", 120) or 120)
        win = max(10, win)
        start = max(0, total - win)
        selected = rows[start:]

        merged_rows: list[dict[str, Any]] = []
        out_lines: list[str] = []
        tier_dist: dict[str, int] = {}
        trust_dist: dict[str, int] = {}
        for row in selected:
            eff = self._effective_level(row)
            merged = {
                **row,
                **eff,
                "rebalance_reason": "recent_window_effective_scoring",
                "rebalance_window": win,
            }
            merged_rows.append(merged)

        shape_enabled = bool(self.rebalance_cfg.get("enabled_distribution_shaping", True))
        if shape_enabled and merged_rows:
            hard_ratio = self._to_float(self.rebalance_cfg.get("hard_top_ratio", 0.10), 0.10)
            hypo_ratio = self._to_float(self.rebalance_cfg.get("hypothesis_bottom_ratio", 0.15), 0.15)
            min_hard = int(self._to_float(self.rebalance_cfg.get("min_hard_count", 1), 1))
            min_hypo = int(self._to_float(self.rebalance_cfg.get("min_hypothesis_count", 1), 1))
            hypo_max = self._to_float(self.rebalance_cfg.get("hypothesis_max_score", 0.70), 0.70)
            n = len(merged_rows)
            hard_slots = max(0, min(n, max(min_hard, int(round(n * hard_ratio)))))
            hypo_slots = max(0, min(n, max(min_hypo, int(round(n * hypo_ratio)))))
            ranked = sorted(
                merged_rows,
                key=lambda x: self._to_float(x.get("effective_score", 0.0), 0.0),
                reverse=True,
            )
            for idx, item in enumerate(ranked):
                trust = str(item.get("effective_trust", "hypothesis"))
                score = self._to_float(item.get("effective_score", 0.0), 0.0)
                if idx < hard_slots and trust == "soft_trust":
                    item["effective_trust"] = "hard_trust"
                    item["effective_tier"] = "core"
                    item["rebalance_reason"] = "distribution_shaping_promote"
                elif idx >= max(0, n - hypo_slots) and trust in {"soft_trust", "hard_trust"} and score <= hypo_max:
                    item["effective_trust"] = "hypothesis"
                    item["effective_tier"] = "observation"
                    item["rebalance_reason"] = "distribution_shaping_demote"
            merged_rows = ranked

        for merged in merged_rows:
            tier = str(merged.get("effective_tier", "observation"))
            trust = str(merged.get("effective_trust", "hypothesis"))
            tier_dist[tier] = tier_dist.get(tier, 0) + 1
            trust_dist[trust] = trust_dist.get(trust, 0) + 1
            out_lines.append(json.dumps(merged, ensure_ascii=False))

        self.rebalance_file.write_text("\n".join(out_lines) + ("\n" if out_lines else ""), encoding="utf-8")
        return {
            "status": "success",
            "total": total,
            "rebalanced": len(selected),
            "window": win,
            "output_file": str(self.rebalance_file),
            "effective_tier_distribution": tier_dist,
            "effective_trust_distribution": trust_dist,
        }

    def build_weekly_threshold_suggestion(self, window: int = 500) -> dict[str, Any]:
        rows = self._raw_rows()
        recent = rows[-max(20, int(window)) :] if rows else []
        if not recent:
            return {"status": "empty", "window": int(window), "suggestions": []}

        used = [r for r in recent if bool(r.get("used_in_answer", False))]
        wasted = [r for r in recent if int(r.get("retrieval_waste", 0) or 0) > 0]
        hard = [r for r in recent if str(r.get("trust", "")) == "hard_trust"]
        hypo = [r for r in recent if str(r.get("trust", "")) == "hypothesis"]

        used_ratio = len(used) / max(1, len(recent))
        waste_ratio = len(wasted) / max(1, len(recent))
        hard_ratio = len(hard) / max(1, len(recent))
        hypo_ratio = len(hypo) / max(1, len(recent))

        suggestions: list[dict[str, Any]] = []
        if used_ratio < 0.35:
            suggestions.append(
                {
                    "key": "working_memory.dynamic_injection_budget.simple_top_k",
                    "direction": "decrease",
                    "reason": "injected memories are rarely used in answers",
                    "delta": -1,
                }
            )
        if waste_ratio > 0.55:
            suggestions.append(
                {
                    "key": "working_memory.dynamic_injection_budget.low_trust_ratio_cap",
                    "direction": "decrease",
                    "reason": "retrieval waste is high",
                    "delta": -0.05,
                }
            )
        if hard_ratio < 0.10:
            suggestions.append(
                {
                    "key": "knowledge_control.retrieval_mix_balancer.hard_top_ratio",
                    "direction": "increase",
                    "reason": "hard_trust exposure is too low",
                    "delta": 0.05,
                }
            )
        if hypo_ratio > 0.35:
            suggestions.append(
                {
                    "key": "knowledge_control.retrieval_mix_balancer.hypothesis_bottom_ratio",
                    "direction": "decrease",
                    "reason": "hypothesis proportion is too high",
                    "delta": -0.05,
                }
            )

        return {
            "status": "success",
            "window": int(window),
            "stats": {
                "recent_count": len(recent),
                "used_ratio": round(used_ratio, 4),
                "waste_ratio": round(waste_ratio, 4),
                "hard_ratio": round(hard_ratio, 4),
                "hypothesis_ratio": round(hypo_ratio, 4),
            },
            "suggestions": suggestions,
        }
