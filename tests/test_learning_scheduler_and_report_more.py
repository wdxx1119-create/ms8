from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from ms8.engine_core import learning as learning_mod
from ms8.engine_core.learning import MemoryLearning


def _obj(tmp_path: Path) -> MemoryLearning:
    o = MemoryLearning.__new__(MemoryLearning)
    mem = tmp_path / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    o.config = {
        "workspace_dir": tmp_path,
        "memory_dir": mem,
        "settings": {
            "memory": {
                "learning": {"scheduler_poll_seconds": 5, "task_log_file": str(mem / "learning_task_log.jsonl")},
                "compression": {"report_dir": str(mem / "compression_reports"), "quality_threshold": 80},
                "meta_cognition": {"task_log_file": str(mem / "meta_task_log.jsonl"), "enabled": False},
            }
        },
    }
    o.learning_enabled = True
    o.learning_state_file = mem / "learning_runtime_state.json"
    o._instance_id = 1
    o._scheduler_thread = None
    o._log_learning_event = lambda *a, **k: None  # type: ignore[assignment]
    return o


def test_run_daily_learning_with_log_success_skip_error(tmp_path: Path) -> None:
    o = _obj(tmp_path)
    calls: list[str] = []
    target_date = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    o._already_daily_done = lambda d: calls.append(f"check:{d}") or False  # type: ignore[method-assign]
    o.daily_learning_task = lambda: calls.append("run")  # type: ignore[method-assign]
    o._mark_daily_done = lambda d: calls.append(f"mark:{d}")  # type: ignore[method-assign]
    o._log_learning_event = lambda e, s, detail=None: calls.append(f"{e}:{s}")  # type: ignore[assignment]
    o._run_daily_learning_with_log()
    assert f"check:{target_date}" in calls
    assert "run" in calls
    assert f"mark:{target_date}" in calls
    assert "daily_learning:success" in calls

    calls.clear()
    o._already_daily_done = lambda d: True  # type: ignore[method-assign]
    o._run_daily_learning_with_log()
    assert "daily_learning:skipped" in calls

    calls.clear()
    o._already_daily_done = lambda d: False  # type: ignore[method-assign]
    o.daily_learning_task = lambda: (_ for _ in ()).throw(RuntimeError("x"))  # type: ignore[method-assign]
    try:
        o._run_daily_learning_with_log()
    except RuntimeError:
        pass
    assert "daily_learning:error" in calls


def test_run_weekly_compression_with_log_success_error(tmp_path: Path) -> None:
    o = _obj(tmp_path)
    calls: list[str] = []
    o._log_learning_event = lambda e, s, detail=None: calls.append(f"{e}:{s}")  # type: ignore[assignment]
    o.weekly_compression_task = lambda: calls.append("weekly")  # type: ignore[method-assign]
    o._run_weekly_compression_with_log()
    assert "weekly" in calls
    assert "weekly_compression:success" in calls

    calls.clear()
    o.weekly_compression_task = lambda: (_ for _ in ()).throw(RuntimeError("bad"))  # type: ignore[method-assign]
    try:
        o._run_weekly_compression_with_log()
    except RuntimeError:
        pass
    assert "weekly_compression:error" in calls


def test_start_scheduler_loop_reuse_branch(tmp_path: Path) -> None:
    o = _obj(tmp_path)
    calls: list[str] = []
    o._log_learning_event = lambda e, s, detail=None: calls.append(f"{e}:{s}")  # type: ignore[assignment]
    old = MemoryLearning._GLOBAL_SCHEDULER_THREAD
    try:
        class _AliveThread:
            def is_alive(self) -> bool:
                return True

        t = _AliveThread()
        MemoryLearning._GLOBAL_SCHEDULER_THREAD = t
        o._start_scheduler_loop()
        assert o._scheduler_thread is t
        assert "scheduler:reused" in calls
    finally:
        MemoryLearning._GLOBAL_SCHEDULER_THREAD = old


def test_meta_log_path_and_meta_task_log_and_write_report(tmp_path: Path, monkeypatch) -> None:
    o = _obj(tmp_path)
    # meta_log_path empty
    o.config["settings"]["memory"]["meta_cognition"]["task_log_file"] = ""
    assert o._meta_log_path() is None

    # meta_task_log writes JSONL
    rel = "memory/meta_task_log.jsonl"
    o.config["settings"]["memory"]["meta_cognition"]["task_log_file"] = rel
    o._meta_task_log({"k": "v"})
    p = tmp_path / rel
    assert p.exists()
    assert json.loads(p.read_text(encoding="utf-8").splitlines()[-1])["k"] == "v"

    # write compression report triggers warning branch when score below threshold
    monkeypatch.setattr(o, "_compression_quality", lambda before, after: {"score": 50, "missing_markers": ["必须"]})
    warns: list[str] = []
    monkeypatch.setattr(learning_mod.logger, "warning", lambda msg, *args: warns.append(str(msg)))
    rp = o._write_compression_report("before", "after", {"pre_size": 100, "post_size": 50})
    assert rp.exists()
    assert warns
