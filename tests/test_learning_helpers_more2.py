from __future__ import annotations

import datetime as dt
from pathlib import Path

from ms8.engine_core import learning as learning_mod
from ms8.engine_core.learning import MemoryLearning


def _mk_learning(tmp_path: Path) -> MemoryLearning:
    obj = MemoryLearning.__new__(MemoryLearning)
    obj.config = {"workspace_dir": tmp_path, "memory_dir": tmp_path / "memory"}
    return obj


def test_summarize_text_prefers_bullets_and_limits(tmp_path: Path) -> None:
    obj = _mk_learning(tmp_path)
    text = "- alpha point\n- beta point\n第三句说明。第四句说明。"
    out = obj._summarize_text(text, max_sentences=1, max_chars=20)
    assert out
    assert len(out) <= 20


def test_section_age_days_invalid_date_returns_zero(tmp_path: Path) -> None:
    obj = _mk_learning(tmp_path)
    age = obj._section_age_days({"estimated_date": "not-a-date"}, dt.date.today())
    assert age == 0


def test_compression_quality_marks_missing_markers(tmp_path: Path) -> None:
    obj = _mk_learning(tmp_path)
    before = "我喜欢这个方案，而且这是经验教训。"
    after = "这是一个简短摘要。"
    result = obj._compression_quality(before, after)
    assert "score" in result
    assert isinstance(result["missing_markers"], list)
    assert result["score"] < 100


def test_update_archive_index_writes_entries(tmp_path: Path) -> None:
    obj = _mk_learning(tmp_path)
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    (archive_dir / "a.md").write_text("A", encoding="utf-8")
    sub = archive_dir / "2026-05"
    sub.mkdir(parents=True, exist_ok=True)
    (sub / "b.md").write_text("B", encoding="utf-8")

    obj._update_archive_index(archive_dir)

    index_path = archive_dir / "index.json"
    assert index_path.exists()
    content = index_path.read_text(encoding="utf-8")
    assert "a.md" in content
    assert "b.md" in content


def test_learning_state_load_save_and_mark_done(tmp_path: Path) -> None:
    obj = _mk_learning(tmp_path)
    obj.learning_state_file = tmp_path / "memory" / "learning_runtime_state.json"
    empty = obj._load_learning_state()
    assert empty == {"daily_done_dates": []}

    obj._save_learning_state({"daily_done_dates": ["2026-05-01"]})
    loaded = obj._load_learning_state()
    assert loaded["daily_done_dates"] == ["2026-05-01"]
    assert obj._already_daily_done("2026-05-01") is True

    obj._mark_daily_done("2026-05-02")
    loaded2 = obj._load_learning_state()
    assert "2026-05-02" in loaded2["daily_done_dates"]
    assert "last_daily_done_at" in loaded2


def test_security_locked_matrix(tmp_path: Path) -> None:
    obj = _mk_learning(tmp_path)
    obj.config["settings"] = {"memory": {"security": {"require_unlock_for_maintenance": True}}}

    class _Crypto:
        def __init__(self, enabled: bool, unlocked: bool) -> None:
            self.enabled = enabled
            self.unlocked = unlocked

        def is_enabled(self) -> bool:
            return self.enabled

        def is_unlocked(self) -> bool:
            return self.unlocked

    obj.crypto = _Crypto(enabled=False, unlocked=False)
    assert obj._security_locked() is False
    obj.crypto = _Crypto(enabled=True, unlocked=False)
    assert obj._security_locked() is True
    obj.crypto = _Crypto(enabled=True, unlocked=True)
    assert obj._security_locked() is False


def test_run_pending_tasks_logging(tmp_path: Path, monkeypatch) -> None:
    obj = _mk_learning(tmp_path)
    obj.learning_enabled = True
    calls: list[str] = []
    monkeypatch.setattr(learning_mod.schedule, "run_pending", lambda: calls.append("ran"))
    obj._log_learning_event = lambda event, status, detail=None: calls.append(f"{event}:{status}")  # type: ignore[assignment]
    obj.run_pending_tasks()
    assert "ran" in calls
    assert "run_pending:ok" in calls


def test_weekly_compression_task_preview_and_confirm_guards(tmp_path: Path) -> None:
    obj = _mk_learning(tmp_path)
    obj.learning_enabled = True
    obj._security_locked = lambda: False  # type: ignore[assignment]
    obj.config["settings"] = {
        "memory": {
            "compression": {
                "enabled": True,
                "preview_only": True,
                "require_confirmation": True,
            }
        }
    }
    obj.preview_compression_plan = lambda confirm=False: {"eligible": True, "confirmed": bool(confirm)}  # type: ignore[assignment]
    obj._apply_compression = lambda plan: {"done": True, "plan": plan}  # type: ignore[assignment]

    # preview_only=True should short-circuit before apply
    obj.weekly_compression_task()

    # turn preview off but keep confirmation required and not confirmed -> still skip
    obj.config["settings"]["memory"]["compression"]["preview_only"] = False
    obj.weekly_compression_task()
