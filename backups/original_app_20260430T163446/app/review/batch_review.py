from __future__ import annotations

from typing import Dict

from app.review.review_service import ReviewService
from app.schemas.review_schema import BatchReviewResult


class BatchReview:
    def __init__(self, service: ReviewService) -> None:
        self.service = service

    @staticmethod
    def _risk_rank(risk: str) -> int:
        r = str(risk).lower()
        if r == "low":
            return 0
        if r == "medium":
            return 1
        if r == "high":
            return 2
        if r == "critical":
            return 3
        return 2

    def apply(
        self,
        mode: str = "accept_all",
        limit: int | None = None,
        accept_conf_min: float = 0.62,
        reject_conf_max: float = 0.20,
        per_category_limit: int | None = None,
        drain_reject_conf_max: float = 0.50,
    ) -> BatchReviewResult:
        items = self.service.list_pending()
        # Risk layering: low-risk first, then medium/high, stable by confidence.
        items = sorted(items, key=lambda x: (self._risk_rank(getattr(x, "risk_level", "medium")), -float(getattr(x, "confidence", 0.0) or 0.0)))

        max_items = len(items) if (limit is None or int(limit) <= 0) else min(len(items), int(limit))
        max_per_cat = int(per_category_limit) if (per_category_limit is not None and int(per_category_limit) > 0) else 0

        reviewed_items = []
        accepted = 0
        rejected = 0
        processed = 0
        category_processed: Dict[str, int] = {}

        for item in items:
            if processed >= max_items:
                break

            category = str(getattr(item, "category", "") or "unknown")
            if max_per_cat > 0 and category_processed.get(category, 0) >= max_per_cat:
                continue

            changed = False
            if mode == "accept_all":
                self.service.update(item.memory_id, "accepted")
                accepted += 1
                changed = True
            elif mode == "accept_low_risk":
                # Conservative auto-review: only low-risk + decent confidence items are auto-accepted.
                if str(item.risk_level).lower() == "low" and float(item.confidence or 0.0) >= float(accept_conf_min):
                    self.service.update(item.memory_id, "accepted")
                    accepted += 1
                    changed = True
                else:
                    continue
            elif mode == "triage_default":
                conf = float(item.confidence or 0.0)
                risk = str(item.risk_level).lower()
                # low-risk high-confidence -> accept
                if risk == "low" and conf >= float(accept_conf_min):
                    self.service.update(item.memory_id, "accepted")
                    accepted += 1
                    changed = True
                # very low-confidence items -> reject (all risks)
                elif conf <= float(reject_conf_max):
                    self.service.update(item.memory_id, "rejected")
                    rejected += 1
                    changed = True
                else:
                    continue
            elif mode == "drain_backlog":
                conf = float(item.confidence or 0.0)
                # Conservative drain: only reject obviously low-confidence items.
                if conf <= float(drain_reject_conf_max):
                    self.service.update(item.memory_id, "rejected")
                    rejected += 1
                    changed = True
                else:
                    continue
            else:
                self.service.update(item.memory_id, "rejected")
                rejected += 1
                changed = True

            if changed:
                reviewed_items.append(item)
                processed += 1
                category_processed[category] = category_processed.get(category, 0) + 1

        return BatchReviewResult(reviewed=len(reviewed_items), accepted=accepted, rejected=rejected, items=reviewed_items)

    def relabel(self, memory_id: str, new_category: str, notes: str = "") -> bool:
        return self.service.update(memory_id, "accepted", notes=notes, category=new_category)
