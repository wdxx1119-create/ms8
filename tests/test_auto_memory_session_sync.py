from __future__ import annotations

import json
from pathlib import Path

from ms8.engine_core.auto_memory import AutoMemoryExtractor


class _FakeMemoryCore:
    def __init__(self, workspace: Path) -> None:
        self.config = {
            "workspace_dir": workspace,
            "settings": {
                "memory": {
                    "auto_memory": {
                        "enabled": True,
                        "use_llm": False,
                        "log_file": str(workspace / "memory" / "auto_memory_log.json"),
                        "session_ingestion": {
                            "enabled": True,
                            "sessions_dir": str(workspace / "sessions"),
                            "sessions_dirs_glob": str(workspace / "sessions"),
                            "state_file": str(workspace / "memory" / "openclaw_session_ingest_state.json"),
                            "sync_interval_seconds": 0,
                            "process_timeout_seconds": 8,
                            "max_messages_per_run": 10,
                            "scan_limit_files": 10,
                            "allowed_roles": ["user"],
                        },
                    },
                    "working_memory": {},
                }
            },
        }


def _write_session_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"type": "session", "id": "s1"},
        {"type": "message", "id": "m1", "message": {"role": "user", "content": "请记住这个稳定偏好信息"}} ,
    ]
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def test_sync_openclaw_sessions_skips_when_lock_is_held(tmp_path: Path) -> None:
    workspace = tmp_path
    core = _FakeMemoryCore(workspace)
    extractor = AutoMemoryExtractor(core)
    extractor.pipeline = object()
    _write_session_file(workspace / "sessions" / "a.jsonl")
    extractor.session_lock_dir.mkdir(parents=True, exist_ok=True)
    extractor.session_lock_info_file.write_text(
        json.dumps({"pid": 999999, "started_at": extractor._utc_now_iso()}, ensure_ascii=False),
        encoding="utf-8",
    )

    out = extractor.sync_openclaw_sessions(force=True)

    assert out["status"] == "skipped"
    assert out["reason"] == "session_sync_concurrent"
    assert out["lock_acquired"] is False


def test_sync_openclaw_sessions_saves_checkpoint_after_success(tmp_path: Path) -> None:
    workspace = tmp_path
    core = _FakeMemoryCore(workspace)
    extractor = AutoMemoryExtractor(core)
    extractor.pipeline = object()
    session_file = workspace / "sessions" / "a.jsonl"
    _write_session_file(session_file)
    calls: list[tuple[str, str]] = []
    extractor._process_interaction_with_timeout = lambda text, source="interaction": calls.append((text, source)) or True

    out = extractor.sync_openclaw_sessions(force=True, max_messages=1)

    assert out["status"] == "session_sync"
    assert out["lock_acquired"] is True
    assert out["processed"] == 1
    assert out["truncated"] is True
    assert calls and calls[0][1].startswith("openclaw_session:")

    state = json.loads(extractor.session_state_file.read_text(encoding="utf-8"))
    assert str(session_file) in state["files"]
    assert int(state["files"][str(session_file)]) >= 1
    assert state["recent_hashes"]
    assert state["last_sync_at"]
