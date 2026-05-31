from __future__ import annotations

import json
from pathlib import Path

import pytest

from ms8.engine_core import learning as learning_mod


class _FakeCrypto:
    def __init__(self, enabled: bool = False, unlocked: bool = True) -> None:
        self._enabled = enabled
        self._unlocked = unlocked

    def is_enabled(self) -> bool:
        return self._enabled

    def is_unlocked(self) -> bool:
        return self._unlocked


class _FakeFileStore:
    def __init__(self, text: str = "") -> None:
        self._text = text

    def read_memory_md(self) -> str:
        return self._text

    def write_memory_md(self, content: str) -> None:
        self._text = content


class _FakeSQLite:
    def cleanup_old_entities(self, retention_days: int) -> int:  # noqa: ARG002
        return 0


class _EveryStub:
    def __init__(self) -> None:
        self.at_time = None
        self.tags: list[str] = []
        self.hours = self
        self.day = self
        self.sunday = self
        self.monday = self

    def at(self, time_str: str):
        self.at_time = time_str
        return self

    def do(self, _fn):  # noqa: ANN001
        return self

    def tag(self, name: str):
        self.tags.append(name)
        return self


class _ScheduleStub:
    def __init__(self) -> None:
        self.items: list[_EveryStub] = []
        self.every_calls: list[int | None] = []

    def every(self, n: int | None = None):
        self.every_calls.append(n)
        item = _EveryStub()
        self.items.append(item)
        return item


def _mk_cfg(tmp_path: Path) -> dict:
    mem = tmp_path / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    return {
        "workspace_dir": tmp_path,
        "memory_dir": mem,
        "daily_dir": mem / "daily",
        "settings": {
            "memory": {
                "learning": {
                    "enabled": True,
                    "daily_summary_time": "03:00",
                    "compression_day": "Sunday",
                    "retention_days": 30,
                    "allow_learning_execute_tiering": False,
                    "task_log_file": str(mem / "learning_task_log.jsonl"),
                    "scheduler_poll_seconds": 5,
                    "auto_review_enabled": True,
                    "auto_review_interval_hours": 4,
                    "context_opt_enabled": True,
                    "context_opt_interval_hours": 6,
                },
                "meta_cognition": {
                    "enabled": True,
                    "task_log_file": str(mem / "meta_task_log.jsonl"),
                    "bootstrap_cooldown_seconds": 3600,
                    "monitor_interval_hours": 0,
                    "schedule_time": "04:00",
                },
                "compression": {
                    "enabled": True,
                    "require_confirmation": False,
                    "preview_only": False,
                    "notify_on_compress": False,
                    "report_dir": str(mem / "compression_reports"),
                },
                "security": {"require_unlock_for_maintenance": True},
            }
        },
    }


def _mk_learning(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, fast_start: bool = True):
    cfg = _mk_cfg(tmp_path)
    monkeypatch.setattr(learning_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(learning_mod, "get_crypto_manager", lambda _cfg: _FakeCrypto(False, True))
    if fast_start:
        monkeypatch.setenv("OPENCLAW_MEMORY_FAST_START", "1")
    else:
        monkeypatch.setenv("OPENCLAW_MEMORY_FAST_START", "0")
    obj = learning_mod.MemoryLearning(memory_core=object())
    obj.file_store = _FakeFileStore("## Preamble\nhello\n")  # type: ignore[assignment]
    obj.sqlite_store = _FakeSQLite()  # type: ignore[assignment]
    return obj


def test_setup_scheduled_tasks_register_and_reuse(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    obj = _mk_learning(monkeypatch, tmp_path, fast_start=True)
    sched = _ScheduleStub()
    monkeypatch.setattr(learning_mod, "schedule", sched)
    learning_mod.MemoryLearning._GLOBAL_SCHEDULE_REGISTERED = False
    obj.memory_core = type("M", (), {"batch_review": lambda *a, **k: {}, "get_context_optimization_suggestions": lambda *a, **k: {}})()
    obj._setup_scheduled_tasks()
    assert learning_mod.MemoryLearning._GLOBAL_SCHEDULE_REGISTERED is True
    assert sched.items

    # Reuse path
    obj._setup_scheduled_tasks()
    assert learning_mod.MemoryLearning._GLOBAL_SCHEDULE_REGISTERED is True


def test_start_scheduler_loop_reuse_existing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    obj = _mk_learning(monkeypatch, tmp_path, fast_start=True)

    class _AliveThread:
        def is_alive(self) -> bool:
            return True

    learning_mod.MemoryLearning._GLOBAL_SCHEDULER_THREAD = _AliveThread()  # type: ignore[assignment]
    obj._start_scheduler_loop()
    assert obj._scheduler_thread is learning_mod.MemoryLearning._GLOBAL_SCHEDULER_THREAD


def test_meta_paths_and_bootstrap_skip_or_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    obj = _mk_learning(monkeypatch, tmp_path, fast_start=True)
    p = obj._meta_log_path()
    assert p is not None and p.parent.exists()

    # disabled meta branch
    obj.config["settings"]["memory"]["meta_cognition"]["enabled"] = False
    obj._maybe_bootstrap_meta_task()

    # enabled but memory_core unavailable branch
    obj.config["settings"]["memory"]["meta_cognition"]["enabled"] = True
    obj.memory_core = None
    obj._maybe_bootstrap_meta_task()

    # cooldown skip branch
    obj.memory_core = object()
    log_path = obj._meta_log_path()
    assert log_path is not None
    log_path.write_text(
        json.dumps({"timestamp": learning_mod.datetime.datetime.now().isoformat(), "status": "ok"}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    obj._maybe_bootstrap_meta_task()

    # malformed log fallback to run task
    log_path.write_text("{bad-json}\n", encoding="utf-8")
    called = {"n": 0}
    monkeypatch.setattr(obj, "_run_meta_cognition_task", lambda: called.__setitem__("n", called["n"] + 1))
    obj._maybe_bootstrap_meta_task()
    assert called["n"] == 1


def test_meta_task_log_handles_write_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    obj = _mk_learning(monkeypatch, tmp_path, fast_start=True)
    monkeypatch.setattr(learning_mod, "guarded_file_write", lambda *_a, **_k: (_ for _ in ()).throw(OSError("no write")))
    obj._meta_task_log({"x": 1})  # should not raise


def test_run_meta_cognition_task_success_and_skipped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    obj = _mk_learning(monkeypatch, tmp_path, fast_start=True)
    obj.memory_core = object()
    obj._run_meta_cognition_task()  # skipped meta_api_unavailable

    class _Core:
        def run_meta_cognition(self):
            return {"ok": True}

    obj.memory_core = _Core()
    obj._run_meta_cognition_task()


def test_section_age_and_compress_compat_typeerror(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    obj = _mk_learning(monkeypatch, tmp_path, fast_start=True)
    # invalid date branch
    age = obj._section_age_days({"estimated_date": "invalid-date"}, learning_mod.datetime.date.today())
    assert age == 0

    # _summarize_text old signature compatibility branch
    monkeypatch.setattr(obj, "_summarize_text", lambda text, max_sentences=2: f"sum:{text[:5]}")  # type: ignore[assignment]
    mem = (
        "## Verification - a\nline one\n"
        "\n## Learning Summary - 2000-01-01\nold and very long text " + ("x" * 220)
    )
    out = obj._compress_memory_sections(mem)
    assert "summarized_sections" in out


def test_apply_compression_redact_import_fail(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    obj = _mk_learning(monkeypatch, tmp_path, fast_start=True)
    monkeypatch.setattr(obj, "_compress_memory_sections", lambda _m: {
        "compressed_memory": "## Preamble\nx\n",
        "removed_sections": [],
        "summarized_sections": [],
        "merged_duplicates": [],
        "kept_sections": ["Preamble"],
        "section_count": 1,
    })
    monkeypatch.setattr(obj, "trigger_memory_tiering", lambda _r: [])
    monkeypatch.setattr(obj, "_write_compression_report", lambda *_a, **_k: tmp_path / "r.json")
    # force import failure path for redact_sensitive_text
    real_import = __import__

    def _patched_import(name, *args, **kwargs):  # noqa: ANN001
        if name.endswith("admission_compat"):
            raise ImportError("blocked")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(learning_mod, "__import__", _patched_import, raising=False)
    result = obj._apply_compression({"items": []})
    assert result["post_size"] >= 1
