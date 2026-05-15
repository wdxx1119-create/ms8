from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class BatchReviewResult:
    reviewed: int
    accepted: int
    rejected: int


class BatchReview:
    def __init__(self, review_service: Any) -> None:
        self.review_service = review_service

    def apply(self, mode: str = "accept_all") -> BatchReviewResult:
        if str(mode) == "accept_all":
            out = self.review_service.apply_decision_all_pending("accepted")
            reviewed = int(out.get("reviewed", 0))
            return BatchReviewResult(reviewed=reviewed, accepted=reviewed, rejected=0)
        if str(mode) == "reject_all":
            out = self.review_service.apply_decision_all_pending("rejected")
            reviewed = int(out.get("reviewed", 0))
            return BatchReviewResult(reviewed=reviewed, accepted=0, rejected=reviewed)
        out = self.review_service.apply_decision_all_pending("pending")
        reviewed = int(out.get("reviewed", 0))
        return BatchReviewResult(reviewed=reviewed, accepted=0, rejected=0)
