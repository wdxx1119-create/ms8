import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memory.auto_memory import AutoMemoryExtractor


class _FakeMemoryCore:
    def __init__(self, workspace: Path) -> None:
        self.config = {
            "workspace_dir": workspace,
            "settings": {
                "memory": {
                    "auto_memory": {
                        "enabled": True,
                        "min_confidence": 0.55,
                        "max_per_interaction": 3,
                        "use_llm": False,
                        "validate": True,
                        "allow_categories": [],
                        "cooldown_minutes": 0,
                        "log_file": str(workspace / "memory" / "auto_memory_log.json"),
                    }
                }
            },
        }
        self.remember_calls = []

    def remember(self, instruction, content=None, auto_generate_reason=True, validate=True, use_llm=True):
        self.remember_calls.append((instruction, content))
        return {"status": "success"}


class AutoMemoryTests(unittest.TestCase):
    def test_extracts_preference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "memory").mkdir(parents=True, exist_ok=True)
            core = _FakeMemoryCore(workspace)
            extractor = AutoMemoryExtractor(core)
            extractor.process_interaction("我比较喜欢 2 空格缩进", source="interaction")
            self.assertTrue(core.remember_calls)


if __name__ == "__main__":
    unittest.main()
