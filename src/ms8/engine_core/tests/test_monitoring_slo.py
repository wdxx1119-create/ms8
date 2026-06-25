import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import ModuleType

MONITORING_FILE = Path(__file__).resolve().parents[1] / "monitoring.py"
spec = importlib.util.spec_from_file_location("memory_monitoring_module", MONITORING_FILE)
assert spec and spec.loader
module: ModuleType = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
MemoryMonitoring = module.MemoryMonitoring


class MonitoringSLOTests(unittest.TestCase):
    def test_status_writes_reports_with_slo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            memory_dir = workspace / "memory"
            memory_dir.mkdir(parents=True, exist_ok=True)

            (memory_dir / "auto_memory_log.json").write_text(
                json.dumps({"entries": [{"status": "success"}, {"status": "success"}]}),
                encoding="utf-8",
            )
            (memory_dir / "memory_usage_log.jsonl").write_text(
                json.dumps({"injected_count": 1}) + "\n",
                encoding="utf-8",
            )
            (memory_dir / "maintenance_state.json").write_text(
                json.dumps({"last_backup_at": "2026-01-01T00:00:00"}),
                encoding="utf-8",
            )

            config = {
                "workspace_dir": workspace,
                "memory_dir": memory_dir,
                "settings": {
                    "memory": {
                        "monitoring": {
                            "enabled": True,
                            "slo": {
                                "capture_rate_min": 0.8,
                                "injection_rate_min": 0.5,
                                "duplicate_drop_rate_max": 0.3,
                                "backup_success_rate_min": 1.0,
                            },
                            "daily_report_file": str(memory_dir / "health_report_latest.json"),
                            "daily_report_markdown": str(memory_dir / "health_report_latest.md"),
                        }
                    }
                },
            }
            mon = MemoryMonitoring(config)
            snapshot = mon.status()
            self.assertTrue(snapshot.get("enabled"))
            self.assertIn("slo", snapshot)
            self.assertTrue(Path(snapshot["report_paths"]["json"]).exists())
            self.assertTrue(Path(snapshot["report_paths"]["markdown"]).exists())

    def test_rates_v2_reads_text_from_nested_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            memory_dir = workspace / "memory"
            memory_dir.mkdir(parents=True, exist_ok=True)

            (memory_dir / "auto_memory_log.json").write_text(
                json.dumps(
                    {
                        "entries": [
                            {
                                "status": "success",
                                "records": [
                                    {
                                        "text": "请查看 pypl 下载量统计网址并记录配置步骤",
                                        "category": "configuration",
                                        "status": "accepted",
                                    }
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            config = {
                "workspace_dir": workspace,
                "memory_dir": memory_dir,
                "settings": {"memory": {"monitoring": {"enabled": True}}},
            }
            mon = MemoryMonitoring(config)
            snapshot = mon.status(persist_reports=False)
            rates_v2 = snapshot.get("rates_v2", {})
            self.assertEqual(rates_v2.get("eligible_events"), 1)
            self.assertEqual(rates_v2.get("low_value_events"), 0)
            self.assertEqual(rates_v2.get("valuable_capture_rate"), 1.0)
            self.assertEqual(rates_v2.get("noise_block_rate"), 1.0)


if __name__ == "__main__":
    unittest.main()
