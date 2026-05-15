import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.memory.indexer import MemoryIndexer
from memory.maintenance_manager import MaintenanceManager


class _FakeFileStore:
    def __init__(self, memory_md: Path):
        self._path = memory_md

    def read_memory_md(self) -> str:
        if self._path.exists():
            return self._path.read_text(encoding="utf-8")
        return "# MEMORY\n"

    def write_memory_md(self, content: str) -> None:
        self._path.write_text(content, encoding="utf-8")


class CLifecycleOpsTests(unittest.TestCase):
    def test_hot_cold_index_search(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "idx.json"
            idx = MemoryIndexer(p)
            idx._container_mode = "dict"
            idx.add({"normalized_text": "alpha hot", "confidence": 0.9, "created_at": "2026-04-14T00:00:00"})
            idx.add({"normalized_text": "beta cold", "confidence": 0.2, "created_at": "2025-01-01T00:00:00"})
            hits = idx.search("alpha", limit=3)
            self.assertTrue(hits and "alpha" in hits[0]["normalized_text"])
            payload = json.loads(p.read_text(encoding="utf-8"))
            self.assertIn("hot_items", payload)
            self.assertIn("cold_items", payload)

    def test_restore_drill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            memory_dir = ws / "memory"
            memory_dir.mkdir(parents=True, exist_ok=True)
            (ws / "MEMORY.md").write_text("# MEMORY", encoding="utf-8")
            (memory_dir / "memory.db").write_text("db", encoding="utf-8")
            cfg = {
                "workspace_dir": ws,
                "memory_dir": memory_dir,
                "memory_md": ws / "MEMORY.md",
                "settings": {
                    "memory": {
                        "maintenance": {
                            "enabled": True,
                            "backup_enabled": True,
                            "backup_interval_hours": 24,
                            "backup_dir": str(memory_dir / "backups"),
                            "backup_keep": 7,
                            "sync_memory_md": False,
                            "cleanup_enabled": False,
                            "restore_drill_enabled": True,
                            "restore_drill_interval_days": 7,
                            "restore_drill_keep_reports": 4,
                            "state_file": str(memory_dir / "maintenance_state.json"),
                        }
                    }
                },
            }
            mm = MaintenanceManager(cfg, _FakeFileStore(ws / "MEMORY.md"))
            mm.backup_assets()
            result = mm.run_restore_drill()
            self.assertIn(result.get("status"), {"success", "skipped"})


if __name__ == "__main__":
    unittest.main()
