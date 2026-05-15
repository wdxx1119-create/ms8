import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ms8.engine_core.synthetic_memory import MemorySynthesizer


class _FakeGraph:
    def list_relations(self, limit=20):
        return [
            {
                "id": "r1",
                "relation_type": "uses",
                "subject_name": "project",
                "object_name": "python",
                "strength": 0.8,
                "confidence": 0.9,
                "source_memory_ref": "x",
            }
        ]

    def search_entities(self, name, limit=1):
        return [{"importance": 0.8}]

    def gap_report(self, min_importance=0.6, max_relations=1, limit=10):
        return []


class _FakeCore:
    def __init__(self, workspace: Path):
        self.config = {
            "memory_dir": workspace / "memory",
            "settings": {
                "memory": {
                    "synthetic_memory": {
                        "enabled": True,
                        "reasoning_only_mode": True,
                        "promotion_min_hits": 2,
                        "max_rebuttal_before_reject": 2,
                        "max_candidates": 5,
                        "min_relation_strength": 0.6,
                        "allowed_relations": ["uses"],
                        "quality_thresholds": {
                            "confidence": 0.5,
                            "consistency": 0.5,
                            "novelty": 0.1,
                            "usefulness": 0.1,
                        },
                        "auto_accept_threshold": 0.7,
                    }
                }
            },
        }
        self.knowledge_graph = _FakeGraph()
        self.file_store = type("FS", (), {"read_memory_md": lambda self: ""})()

    def remember(self, instruction, content=None, auto_generate_reason=True, validate=True, use_llm=False):
        return {"status": "success"}


class SyntheticReasoningTests(unittest.TestCase):
    def test_reasoning_candidate_promotion_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            (ws / "memory").mkdir(parents=True, exist_ok=True)
            core = _FakeCore(ws)
            syn = MemorySynthesizer(core)
            cands = syn.generate_candidates(limit=2)
            self.assertTrue(cands)
            cid = cands[0]["candidate_id"]
            self.assertEqual(cands[0]["status"], "candidate_reasoning_only")
            syn.record_candidate_hits([cid], used=True, rebuttal=False)
            out = syn.record_candidate_hits([cid], used=True, rebuttal=False)
            self.assertGreaterEqual(out["promotion_ready"], 1)
            ready = syn.list_candidates(status="promotion_ready", limit=10)
            self.assertTrue(any(x["candidate_id"] == cid for x in ready))


if __name__ == "__main__":
    unittest.main()
