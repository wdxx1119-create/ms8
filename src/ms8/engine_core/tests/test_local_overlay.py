import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ms8.engine_core.priority_engine import ConfigPriorityEngine


class LocalOverlayTests(unittest.TestCase):
    def test_gitignore_created(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            skill_root = workspace / "skill"
            (skill_root / "references").mkdir(parents=True, exist_ok=True)
            (skill_root / "references" / "admin_defaults.yaml").write_text("", encoding="utf-8")
            default_cfg: dict[str, object] = {"config_layers": {"protected_paths": []}}
            engine = ConfigPriorityEngine(workspace, skill_root, default_cfg)
            engine.ensure_local_overlay_support()
            gitignore = (workspace / ".gitignore").read_text(encoding="utf-8")
            self.assertIn("config.local.yaml", gitignore)


if __name__ == "__main__":
    unittest.main()
