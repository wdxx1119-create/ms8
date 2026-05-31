from __future__ import annotations

import datetime as dt
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
    def __init__(self, memory_text: str = "") -> None:
        self._text = memory_text

    def read_memory_md(self) -> str:
        return self._text

    def write_memory_md(self, content: str) -> None:
        self._text = content


class _FakeSqliteStore:
    def __init__(self) -> None:
        self.entities: list[tuple[str, str]] = []
        self.relations: list[tuple[str, str, str, float]] = []

    def add_entity(self, name: str, kind: str) -> None:
        self.entities.append((name, kind))

    def add_relation(self, s: str, p: str, o: str, strength: float = 0.5) -> None:
        self.relations.append((s, p, o, strength))

    def cleanup_old_entities(self, retention_days: int) -> int:  # noqa: ARG002
        return 2


class _FakeKG:
    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled
        self.settings = {"batch_size": 3}
        self.calls: list[str] = []

    def is_enabled(self) -> bool:
        return self._enabled

    def batch_extract_pending_memories(self, limit: int) -> None:
        self.calls.append(f"batch:{limit}")

    def decay_relation_weights(self) -> None:
        self.calls.append("decay")

    def cleanup_isolated_entities(self) -> None:
        self.calls.append("cleanup")


def _mk_learning(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> learning_mod.MemoryLearning:
    mem = tmp_path / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    cfg = {
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
                },
                "security": {"require_unlock_for_maintenance": True},
                "compression": {
                    "enabled": True,
                    "require_confirmation": False,
                    "preview_only": False,
                    "notify_on_compress": False,
                    "report_dir": str(mem / "compression_reports"),
                },
            }
        },
    }
    monkeypatch.setenv("OPENCLAW_MEMORY_FAST_START", "1")
    monkeypatch.setattr(learning_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(learning_mod, "get_crypto_manager", lambda _cfg: _FakeCrypto(False, True))
    obj = learning_mod.MemoryLearning()
    obj.file_store = _FakeFileStore("## Preamble\nhello\n")  # type: ignore[assignment]
    obj.sqlite_store = _FakeSqliteStore()  # type: ignore[assignment]
    return obj


def test_trigger_weekly_compression_status_matrix(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    obj = _mk_learning(monkeypatch, tmp_path)
    obj._security_locked = lambda: True  # type: ignore[assignment]
    assert obj.trigger_weekly_compression()["status"] == "blocked"

    obj._security_locked = lambda: False  # type: ignore[assignment]
    obj.learning_enabled = False
    assert obj.trigger_weekly_compression()["status"] == "disabled"

    obj.learning_enabled = True
    monkeypatch.setattr(obj, "preview_compression_plan", lambda confirm=False: {"eligible": False, "confirm": confirm})
    out = obj.trigger_weekly_compression(preview_only=True)
    assert out["status"] == "preview"
    assert out["plan"]["eligible"] is False


def test_trigger_weekly_compression_needs_confirm_and_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    obj = _mk_learning(monkeypatch, tmp_path)
    obj.config["settings"]["memory"]["compression"]["require_confirmation"] = True
    monkeypatch.setattr(obj, "preview_compression_plan", lambda confirm=False: {"eligible": True, "confirm": confirm})
    needs = obj.trigger_weekly_compression(confirm=False)
    assert needs["status"] == "needs_confirmation"

    obj.config["settings"]["memory"]["compression"]["require_confirmation"] = False
    monkeypatch.setattr(obj, "_apply_compression", lambda _plan: {"report_path": "x", "pre_size": 10, "post_size": 5})
    ok = obj.trigger_weekly_compression(confirm=True)
    assert ok["status"] == "success"
    assert ok["result"]["post_size"] == 5


def test_trigger_daily_learning_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    obj = _mk_learning(monkeypatch, tmp_path)
    obj._security_locked = lambda: True  # type: ignore[assignment]
    obj.trigger_daily_learning("2026-05-01")

    obj._security_locked = lambda: False  # type: ignore[assignment]
    obj._already_daily_done = lambda _d: True  # type: ignore[assignment]
    obj.trigger_daily_learning("2026-05-01")

    obj._already_daily_done = lambda _d: False  # type: ignore[assignment]
    obj.analyze_daily_log = lambda _d: {}  # type: ignore[assignment]
    obj.trigger_daily_learning("2026-05-01")

    kg = _FakeKG(enabled=True)
    obj.knowledge_graph = kg
    obj.analyze_daily_log = lambda _d: {"entities": ["A"], "relations": [("A", "r", "B")]}  # type: ignore[assignment]
    obj.trigger_daily_learning("2026-05-01")
    assert ("A", "learned") in obj.sqlite_store.entities
    assert kg.calls == ["batch:3"]


def test_run_auto_review_and_context_opt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class _Core:
        def __init__(self) -> None:
            self.called = 0

        def batch_review(self, **kwargs):  # noqa: ANN003
            self.called += 1
            return {"ok": True, "kwargs": kwargs}

        def get_context_optimization_suggestions(self, window: int) -> dict[str, int]:
            return {"window": window}

    obj = _mk_learning(monkeypatch, tmp_path)
    core = _Core()
    obj.memory_core = core
    obj._run_auto_review_task()
    assert core.called == 1
    obj._run_context_optimization_task()


def test_run_meta_cognition_task_retry_then_stop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class _Core:
        def run_meta_cognition(self):
            raise RuntimeError("boom")

    obj = _mk_learning(monkeypatch, tmp_path)
    obj.memory_core = _Core()
    monkeypatch.setattr(learning_mod.time, "sleep", lambda _x: None)
    obj._run_meta_cognition_task()


def test_trigger_memory_tiering_plan_and_execute(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    obj = _mk_learning(monkeypatch, tmp_path)
    daily = obj.config["daily_dir"]
    daily.mkdir(parents=True, exist_ok=True)
    old_date = (dt.date.today() - dt.timedelta(days=40)).isoformat()
    old_log = daily / f"{old_date}-x.md"
    old_log.write_text("old", encoding="utf-8")

    plan = obj.build_memory_tiering_plan(retention_days=30)
    assert plan and plan[0]["name"] == old_log.name

    planned_only = obj.trigger_memory_tiering(retention_days=30)
    assert planned_only == []

    obj.config["settings"]["memory"]["learning"]["allow_learning_execute_tiering"] = True
    moved = obj.trigger_memory_tiering(retention_days=30)
    assert moved == [old_log.name]
    assert not old_log.exists()
    manifest = obj.config["memory_dir"] / "archive" / "manifest.json"
    assert manifest.exists()


def test_archive_helpers_and_quality(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    obj = _mk_learning(monkeypatch, tmp_path)
    archive = obj.config["memory_dir"] / "archive" / "2026-01"
    archive.mkdir(parents=True, exist_ok=True)
    (archive / "2026-01-01-a.md").write_text("x", encoding="utf-8")
    obj._update_archive_index(obj.config["memory_dir"] / "archive")
    idx = obj.config["memory_dir"] / "archive" / "index.json"
    assert idx.exists()

    restored = obj.restore_archived_logs(date_prefix="2026-01-01", limit=1)
    assert restored == ["2026-01-01-a.md"]
    listed = obj.list_archived_logs(limit=10)
    assert listed and listed[0]["name"].endswith(".md")

    quality = obj._compression_quality("我喜欢这个，必须保存", "我喜欢这个")
    assert quality["score"] < 100
    assert "必须" in quality["missing_markers"]


def test_daily_learning_task_writes_summary_and_kg_ingest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class _FakeDecision:
        should_write_memory_md = True
        normalized_text = "\n## Learning Summary - 2099-01-01\nsafe summary"

    obj = _mk_learning(monkeypatch, tmp_path)
    obj._security_locked = lambda: False  # type: ignore[assignment]
    obj.learning_enabled = True
    obj.analyze_daily_log = lambda _d: {"entities": ["A"], "relations": [("A", "r", "B")], "summary": "S"}  # type: ignore[assignment]
    monkeypatch.setattr(learning_mod, "evaluate_candidate", lambda *_a, **_k: _FakeDecision(), raising=False)
    monkeypatch.setattr(learning_mod, "redact_sensitive_text", lambda txt: {"redacted_text": txt}, raising=False)
    kg = _FakeKG(enabled=True)
    kg.ingested = []  # type: ignore[attr-defined]

    def _ingest(**kwargs):  # noqa: ANN003
        kg.ingested.append(kwargs)  # type: ignore[attr-defined]

    kg.ingest_memory = _ingest  # type: ignore[attr-defined]
    obj.knowledge_graph = kg
    yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    daily = obj.config["daily_dir"]
    daily.mkdir(parents=True, exist_ok=True)
    (daily / f"{yesterday}.md").write_text("daily", encoding="utf-8")
    obj.daily_learning_task()
    assert "Learning Summary" in obj.file_store.read_memory_md()
    assert len(kg.ingested) == 1  # type: ignore[attr-defined]


def test_weekly_compression_task_guards_and_apply(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    obj = _mk_learning(monkeypatch, tmp_path)
    obj._security_locked = lambda: True  # type: ignore[assignment]
    obj.weekly_compression_task()

    obj._security_locked = lambda: False  # type: ignore[assignment]
    obj.learning_enabled = False
    obj.weekly_compression_task()

    obj.learning_enabled = True
    obj.config["settings"]["memory"]["compression"]["enabled"] = False
    obj.weekly_compression_task()

    obj.config["settings"]["memory"]["compression"]["enabled"] = True
    monkeypatch.setattr(obj, "preview_compression_plan", lambda confirm=False: {"eligible": True, "confirmed": False})
    obj.config["settings"]["memory"]["compression"]["preview_only"] = True
    obj.weekly_compression_task()

    obj.config["settings"]["memory"]["compression"]["preview_only"] = False
    obj.config["settings"]["memory"]["compression"]["require_confirmation"] = True
    obj.weekly_compression_task()

    obj.config["settings"]["memory"]["compression"]["require_confirmation"] = False
    called = {"n": 0}

    def _apply(_plan):  # noqa: ANN001
        called["n"] += 1
        return {"report_path": "x"}

    monkeypatch.setattr(obj, "_apply_compression", _apply)
    obj.weekly_compression_task()
    assert called["n"] == 1


def test_run_pending_tasks_and_security_locked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    obj = _mk_learning(monkeypatch, tmp_path)
    called = {"n": 0}
    monkeypatch.setattr(learning_mod.schedule, "run_pending", lambda: called.__setitem__("n", called["n"] + 1))
    obj.learning_enabled = True
    obj.run_pending_tasks()
    assert called["n"] == 1

    obj.config["settings"]["memory"]["security"]["require_unlock_for_maintenance"] = False
    obj.crypto = _FakeCrypto(enabled=True, unlocked=False)  # type: ignore[assignment]
    assert obj._security_locked() is False
