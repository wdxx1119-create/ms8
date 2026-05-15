import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memory.synthetic_memory import MemorySynthesizer


class _FakeGraph:
    def list_relations(self, limit=10):
        return [
            {
                "id": 1,
                "relation_type": "uses",
                "strength": 0.9,
                "confidence": 0.85,
                "subject_name": "OpenClaw",
                "object_name": "SQLite",
                "source_memory_ref": "file::MEMORY.md",
            }
        ]

    def search_entities(self, name, limit=1):
        return [{"importance": 0.9}]

    def gap_report(self, min_importance=0.6, max_relations=1, limit=10):
        return [
            {"canonical_name": "OpenClaw", "importance": 0.9, "relation_count": 0, "entity_type": "tool"}
        ]


class _FakeFileStore:
    def __init__(self, memory_md: Path) -> None:
        self.memory_md = memory_md

    def read_memory_md(self) -> str:
        return self.memory_md.read_text(encoding="utf-8")


class _FakeCore:
    def __init__(self, workspace: Path) -> None:
        memory_dir = workspace / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        memory_md = workspace / "MEMORY.md"
        memory_md.write_text("# Memory\n", encoding="utf-8")
        self.config = {
            "memory_dir": memory_dir,
            "workspace_dir": workspace,
            "settings": {
                "memory": {
                    "synthetic_memory": {
                        "enabled": True,
                        "max_candidates": 10,
                        "min_relation_strength": 0.6,
                        "allowed_relations": ["uses"],
                        "quality_thresholds": {
                            "consistency": 0.8,
                            "confidence": 0.7,
                            "novelty": 0.5,
                            "usefulness": 0.6,
                        },
                        "auto_accept_threshold": 0.9,
                    }
                }
            },
        }
        self.knowledge_graph = _FakeGraph()
        self.file_store = _FakeFileStore(memory_md)

    def remember(self, instruction, content=None, auto_generate_reason=True, validate=True, use_llm=False):
        return {"status": "success"}


class SyntheticMemoryTests(unittest.TestCase):
    def test_generate_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            core = _FakeCore(Path(tmp))
            synthesizer = MemorySynthesizer(core)
            candidates = synthesizer.generate_candidates(limit=5)
            self.assertTrue(candidates)

    def test_gap_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            core = _FakeCore(Path(tmp))
            synthesizer = MemorySynthesizer(core)
            gaps = synthesizer.discover_gaps(limit=5)
            self.assertTrue(gaps.get("gaps"))


if __name__ == "__main__":
    unittest.main()
