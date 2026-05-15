import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memory.learning import MemoryLearning


class _FakeFileStore:
    def __init__(self, memory_md: Path) -> None:
        self.memory_md = memory_md

    def read_memory_md(self) -> str:
        return self.memory_md.read_text(encoding="utf-8")

    def write_memory_md(self, content: str) -> None:
        self.memory_md.write_text(content, encoding="utf-8")


class _FakeSqliteStore:
    def cleanup_old_entities(self, retention_days: int) -> int:
        return 0


class CompressionTests(unittest.TestCase):
    def test_preview_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            memory_dir = workspace / "memory"
            memory_dir.mkdir(parents=True, exist_ok=True)
            (workspace / "MEMORY.md").write_text(
                "## Learning Summary - 2026-04-01\nOld summary\n",
                encoding="utf-8",
            )
            config = {
                "workspace_dir": workspace,
                "memory_dir": memory_dir,
                "memory_md": workspace / "MEMORY.md",
                "settings": {
                    "memory": {
                        "learning": {"enabled": True, "daily_summary_time": "03:00", "compression_day": "Sunday", "retention_days": 30},
                        "compression": {"enabled": True, "min_age_days": 1, "keep_recent_count": 0, "min_log_count": 0, "report_dir": str(memory_dir / "compression_reports")},
                    }
                },
            }
            learning = MemoryLearning()
            learning.config = config
            learning.file_store = _FakeFileStore(workspace / "MEMORY.md")
            learning.sqlite_store = _FakeSqliteStore()
            plan = learning.preview_compression_plan(confirm=False)
            self.assertTrue(plan["eligible"])


if __name__ == "__main__":
    unittest.main()
