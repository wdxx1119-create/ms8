import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memory.meta_cognition import MetaCognitionSystem


class _FakeLLM:
    async def chat(self, messages, temperature=0.3, max_tokens=50, task_type="general"):
        return "0.8"


class MetaCognitionTests(unittest.TestCase):
    def test_overall_score_weights(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "memory").mkdir(parents=True, exist_ok=True)
            meta = MetaCognitionSystem(llm=_FakeLLM())
            meta.settings["metrics_weights"] = {
                "response_quality": 0.5,
                "response_speed": 0.1,
                "user_satisfaction": 0.1,
                "task_completion": 0.2,
                "learning_efficiency": 0.1,
            }
            metrics = {
                "response_quality": 1.0,
                "response_speed": 0.0,
                "user_satisfaction": 0.0,
                "task_completion": 0.0,
                "learning_efficiency": 0.0,
            }
            score = meta._calculate_overall_score(metrics)
            self.assertGreaterEqual(score, 0.45)


if __name__ == "__main__":
    unittest.main()
