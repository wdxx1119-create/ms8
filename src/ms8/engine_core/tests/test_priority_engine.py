import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ms8.engine_core.priority_engine import ConfigPriorityEngine


class PriorityEngineTests(unittest.TestCase):
    def test_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            skill_root = workspace / "skill"
            (skill_root / "references").mkdir(parents=True, exist_ok=True)
            (skill_root / "references" / "admin_defaults.yaml").write_text(
                "memory:\n  safety:\n    block_remote_upload: true\n"
                "config_layers:\n  protected_paths:\n    - memory.safety.block_remote_upload\n",
                encoding="utf-8",
            )
            default_cfg = {
                "memory": {"safety": {"block_remote_upload": True}},
                "config_layers": {"protected_paths": ["memory.safety.block_remote_upload"]},
            }
            (workspace / "config.yaml").write_text(
                "memory:\n  safety:\n    block_remote_upload: false\n",
                encoding="utf-8",
            )
            engine = ConfigPriorityEngine(workspace, skill_root, default_cfg)
            resolved, report = engine.resolve()
            self.assertTrue(resolved["memory"]["safety"]["block_remote_upload"])
            self.assertTrue(report["blocked_overrides"])


if __name__ == "__main__":
    unittest.main()
