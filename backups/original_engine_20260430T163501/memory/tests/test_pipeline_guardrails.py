import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import DedupeConfig, QualityGateConfig
from app.memory.repository import MemoryRepository
from app.pipeline.dedupe import dedupe_check
from app.pipeline.quality_gate import quality_gate
from app.schemas.pipeline_schema import MemoryRecord


class PipelineGuardrailTests(unittest.TestCase):
    def test_quality_gate_cjk_short_allowed(self) -> None:
        ok, reason = quality_gate("测试通过", QualityGateConfig(min_len_cjk=4, min_len_non_cjk=8))
        self.assertTrue(ok)
        self.assertEqual(reason, "ok")

    def test_dedupe_similar_soft_then_hard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = MemoryRepository(Path(tmp) / "records.jsonl")
            first = MemoryRecord(
                text="配置已修改为 enabled=true",
                normalized_text="配置已修改为 enabled=true",
                category="configuration",
                confidence=0.91,
                meta={"id": "abc", "dedupe_key": "k1"},
            )
            repo.save(first)

            cfg = DedupeConfig(
                similar_soft_threshold=0.6,
                similar_hard_threshold=0.95,
                similar_window_minutes=120,
                category_repeat_thresholds={"configuration": 5},
            )
            allow, _, _, mode, _ = dedupe_check(repo, "configuration", "配置已经修改为 enabled=true", cfg=cfg)
            self.assertTrue(allow)
            self.assertTrue(mode.startswith("soft_duplicate"))

            cfg2 = DedupeConfig(
                similar_soft_threshold=0.5,
                similar_hard_threshold=0.55,
                similar_window_minutes=120,
                category_repeat_thresholds={"configuration": 5},
            )
            allow2, _, _, mode2, _ = dedupe_check(repo, "configuration", "配置已经修改为 enabled=true", cfg=cfg2)
            self.assertFalse(allow2)
            self.assertTrue(mode2.startswith("hard_duplicate"))


if __name__ == "__main__":
    unittest.main()
