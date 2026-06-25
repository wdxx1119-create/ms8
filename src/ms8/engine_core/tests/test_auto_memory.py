import os
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ms8.engine_core.auto_memory import AutoMemoryExtractor


class _FakeMemoryCore:
    def __init__(self, workspace: Path, sessions_dir: Path | None = None) -> None:
        session_root = sessions_dir or (workspace / "sessions")
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
                        "session_ingestion": {
                            "enabled": True,
                            "allowed_roles": ["user"],
                            "sessions_dir": str(session_root),
                            "sessions_dirs_glob": str(session_root),
                            "sync_interval_seconds": 45,
                            "process_timeout_seconds": 8,
                            "lock_stale_seconds": 1,
                        },
                    }
                }
            },
        }
        self.remember_calls: list[tuple[str, str | None]] = []

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

    def test_session_sync_skips_when_lock_owned_by_live_process(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            sessions = workspace / "sessions"
            sessions.mkdir(parents=True, exist_ok=True)
            (workspace / "memory").mkdir(parents=True, exist_ok=True)
            core = _FakeMemoryCore(workspace, sessions)
            extractor = AutoMemoryExtractor(core)
            extractor.pipeline = object()
            extractor.session_lock_dir.mkdir(parents=True, exist_ok=True)
            extractor.session_lock_info_file.write_text(
                '{"pid": %d, "started_at": "%s"}' % (os.getpid(), extractor._utc_now_iso()),
                encoding="utf-8",
            )

            result = extractor.sync_openclaw_sessions(force=True)

            self.assertEqual(result["status"], "skipped")
            self.assertEqual(result["reason"], "session_sync_concurrent")
            self.assertFalse(result["lock_acquired"])
            self.assertEqual(result["instance_pid"], os.getpid())

    def test_session_sync_recovers_stale_lock_and_processes_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            sessions = workspace / "sessions"
            sessions.mkdir(parents=True, exist_ok=True)
            session_file = sessions / "abc.jsonl"
            session_file.write_text(
                '\n'.join(
                    [
                        '{"type":"session","id":"abc"}',
                        '{"type":"message","id":"m1","message":{"role":"user","content":"保留这条真正消息"}}',
                    ]
                )
                + '\n',
                encoding="utf-8",
            )
            (workspace / "memory").mkdir(parents=True, exist_ok=True)
            core = _FakeMemoryCore(workspace, sessions)
            extractor = AutoMemoryExtractor(core)
            extractor.pipeline = object()
            extractor.session_lock_dir.mkdir(parents=True, exist_ok=True)
            extractor.session_lock_info_file.write_text(
                '{"pid": 999999, "started_at": "2000-01-01T00:00:00+00:00"}',
                encoding="utf-8",
            )
            seen: list[tuple[str, str]] = []
            extractor._process_interaction_with_timeout = lambda text, source: seen.append((text, source)) or True

            result = extractor.sync_openclaw_sessions(force=True)

            self.assertEqual(result["status"], "session_sync")
            self.assertTrue(result["lock_acquired"])
            self.assertEqual(result["processed"], 1)
            self.assertEqual(len(seen), 1)
            self.assertFalse(extractor.session_lock_dir.exists())
            self.assertIn(str(session_file), result["state_after_files"])

    def test_session_sync_saves_checkpoint_after_each_processed_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            sessions = workspace / "sessions"
            sessions.mkdir(parents=True, exist_ok=True)
            session_file = sessions / "abc.jsonl"
            session_file.write_text(
                '\n'.join(
                    [
                        '{"type":"session","id":"abc"}',
                        '{"type":"message","id":"m1","message":{"role":"user","content":"第一条有效用户消息内容"}}',
                        '{"type":"message","id":"m2","message":{"role":"user","content":"第二条有效用户消息内容"}}',
                    ]
                )
                + '\n',
                encoding="utf-8",
            )
            (workspace / "memory").mkdir(parents=True, exist_ok=True)
            core = _FakeMemoryCore(workspace, sessions)
            extractor = AutoMemoryExtractor(core)
            extractor.pipeline = object()
            call_count = {"value": 0}

            def fake_process(text: str, source: str) -> bool:
                call_count["value"] += 1
                if call_count["value"] == 1:
                    return True
                raise RuntimeError("stop after first success")

            extractor._process_interaction_with_timeout = fake_process

            with self.assertRaises(RuntimeError):
                extractor.sync_openclaw_sessions(force=True)

            state = extractor._load_session_state()
            self.assertEqual(call_count["value"], 2)
            self.assertEqual(state["files"][str(session_file)], 2)
            self.assertEqual(len(state["recent_hashes"]), 1)

    def test_session_sync_summary_records_state_before_and_after(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            sessions = workspace / "sessions"
            sessions.mkdir(parents=True, exist_ok=True)
            session_file = sessions / "abc.jsonl"
            session_file.write_text(
                '\n'.join(
                    [
                        '{"type":"session","id":"abc"}',
                        '{"type":"message","id":"m1","message":{"role":"user","content":"第三条有效用户消息内容"}}',
                    ]
                )
                + '\n',
                encoding="utf-8",
            )
            (workspace / "memory").mkdir(parents=True, exist_ok=True)
            core = _FakeMemoryCore(workspace, sessions)
            extractor = AutoMemoryExtractor(core)
            extractor.pipeline = object()
            extractor._process_interaction_with_timeout = lambda text, source: True

            result = extractor.sync_openclaw_sessions(force=True)

            self.assertEqual(result["status"], "session_sync")
            self.assertEqual(result["instance_pid"], os.getpid())
            self.assertEqual(result["state_before_files"], {})
            self.assertEqual(result["state_after_files"][str(session_file)], 2)


if __name__ == "__main__":
    unittest.main()
