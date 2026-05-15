import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.classifier.threshold_manager import ThresholdManager
from app.config import ThresholdConfig
from app.feedback.feedback_service import FeedbackService
from app.feedback.rule_optimizer import RuleOptimizer
from app.review.batch_review import BatchReview
from app.review.review_service import ReviewService
from app.schemas.feedback_schema import FeedbackItem
from app.schemas.review_schema import ReviewItem


class FeedbackReviewLoopTests(unittest.TestCase):
    def test_feedback_suggestion_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feedback_file = Path(tmp) / "feedback.jsonl"
            service = FeedbackService(store_path=feedback_file)
            for i in range(6):
                service.add(
                    FeedbackItem(
                        memory_id=f"m{i}",
                        signal="explicit_feedback",
                        category="technical_doc",
                        helpful=(i % 2 == 0),
                    )
                )
            tm = ThresholdManager(ThresholdConfig())
            opt = RuleOptimizer(service, tm)
            report_file = Path(tmp) / "weekly.json"
            payload = opt.suggest_threshold_updates(lookback_days=7, min_samples=5, output_path=report_file)
            self.assertTrue(report_file.exists())
            self.assertIn("suggestions", payload)
            self.assertTrue(any(s["category"] == "technical_doc" for s in payload["suggestions"]))

    def test_batch_review_accept(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            queue_file = Path(tmp) / "review.jsonl"
            service = ReviewService(store_path=queue_file)
            service.enqueue(ReviewItem(memory_id="m1", reason="low_confidence", confidence=0.5, category="plan"))
            service.enqueue(ReviewItem(memory_id="m2", reason="conflict", confidence=0.4, category="decision", risk_level="high"))
            runner = BatchReview(service)
            result = runner.apply("accept_all")
            self.assertEqual(result.reviewed, 2)
            self.assertEqual(result.accepted, 2)
            pending = service.list_pending()
            self.assertEqual(len(pending), 0)
            data = queue_file.read_text(encoding="utf-8")
            self.assertIn("\"decision\": \"accepted\"", data)


if __name__ == "__main__":
    unittest.main()
