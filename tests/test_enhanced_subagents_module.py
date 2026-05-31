from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

from ms8.engine_core import enhanced_subagents as mod


class _FakeSkills:
    def list_skills(self):
        return ["a", "b", "c"]


class _FakeMemoryCore:
    def __init__(self):
        self.skills = _FakeSkills()

    def get_recent(self, n=10):
        return [{"content": f"recent-{i}"} for i in range(n)]

    def get_memory_blocks(self):
        return {"human": "h", "persona": "p", "archival": "a"}

    def search(self, _query, top_k=5):
        return [{"source": "s", "title": "t", "content": "c" * 200} for _ in range(top_k)]


def _patch_cfg(monkeypatch, tmp_path, **memory_subcfg):
    cfg = {
        "memory_dir": tmp_path / "mem",
        "workspace_dir": tmp_path / "ws",
        "settings": {"memory": {"subagents": memory_subcfg}},
    }
    cfg["memory_dir"].mkdir(parents=True, exist_ok=True)
    cfg["workspace_dir"].mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(mod, "get_config", lambda: cfg)
    return cfg


def test_subagent_manager_init_and_custom_subagent(monkeypatch, tmp_path):
    cfg = _patch_cfg(monkeypatch, tmp_path, enabled=True)
    # persist one finished task for load path
    tasks_dir = cfg["memory_dir"] / "subagent_tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    (tasks_dir / "task123.json").write_text(
        json.dumps({"status": "completed", "subagent": "explore", "task": "x"}),
        encoding="utf-8",
    )

    manager = mod.SubAgentManager(memory_core=_FakeMemoryCore())
    assert manager.enabled is True
    assert any(a["name"] == "general-purpose" for a in manager.list_subagents())
    assert "task123" in manager.background_tasks

    created = manager.create_custom_subagent("my-agent", "desc", "do x", ["read"])
    assert created["status"] == "success"
    assert any(a.name == "my-agent" for a in manager.custom_subagents)


def test_spawn_disabled_notfound_and_loop(monkeypatch, tmp_path):
    _patch_cfg(monkeypatch, tmp_path, enabled=False)
    manager = mod.SubAgentManager(memory_core=_FakeMemoryCore())
    disabled = asyncio.run(manager.spawn("explore", "abc"))
    assert disabled["status"] == "error"

    manager.enabled = True
    not_found = asyncio.run(manager.spawn("missing", "abc"))
    assert not_found["status"] == "error"

    manager.max_similar_tasks = 0
    looped = asyncio.run(manager.spawn("explore", "abc"))
    assert looped["status"] == "error"


def test_analyze_and_execute_paths(monkeypatch, tmp_path):
    _patch_cfg(monkeypatch, tmp_path, enabled=True)
    manager = mod.SubAgentManager(memory_core=_FakeMemoryCore())

    assert asyncio.run(manager._analyze_task_type("search this"))["type"] == "search"
    assert asyncio.run(manager._analyze_task_type("remember me"))["type"] == "memory"
    assert asyncio.run(manager._analyze_task_type("history trend"))["type"] == "history"
    assert asyncio.run(manager._analyze_task_type("analyze summary"))["type"] == "analysis"
    assert asyncio.run(manager._analyze_task_type("misc"))["type"] == "general"

    gp = asyncio.run(manager._execute_general_purpose("search docs"))
    assert gp["method"] == "explore"
    hist = asyncio.run(manager._execute_history_analyzer("analyze"))
    assert hist["analysis_type"] == "history_pattern"
    init = asyncio.run(manager._execute_init("boot"))
    assert init["status"] == "initialized"

    no_mem = mod.SubAgentManager(memory_core=None)
    assert asyncio.run(no_mem._execute_history_analyzer("x"))["status"] == "error"
    assert asyncio.run(no_mem._execute_init("x"))["status"] == "error"


def test_foreground_retry_timeout_and_concurrency(monkeypatch, tmp_path):
    _patch_cfg(monkeypatch, tmp_path, enabled=True, task_timeout_seconds=1, max_retries=0, max_concurrent=1)
    manager = mod.SubAgentManager(memory_core=_FakeMemoryCore())
    sub = manager._find_subagent("explore")
    assert sub is not None

    async def _slow(_sub, _task):
        await asyncio.sleep(2)
        return {}

    monkeypatch.setattr(manager, "_dispatch_subagent", _slow)
    timed = asyncio.run(manager._spawn_foreground(sub, "x"))
    assert timed["status"] == "error"
    assert timed["error"] == "timeout"

    manager._active_tasks = manager.max_concurrent
    blocked = asyncio.run(manager._spawn_foreground(sub, "x"))
    assert blocked["status"] == "error"
    assert "concurrency" in blocked["error"]


def test_background_spawn_status_and_retry(monkeypatch, tmp_path):
    _patch_cfg(monkeypatch, tmp_path, enabled=True, max_background=1)
    manager = mod.SubAgentManager(memory_core=_FakeMemoryCore())
    sub = manager._find_subagent("explore")
    assert sub is not None

    launched = asyncio.run(manager._spawn_background(sub, "bg task"))
    assert launched["status"] == "launched"
    task_id = launched["task_id"]
    # wait background thread write result
    for _ in range(30):
        status = manager.get_background_task_status(task_id)
        if status["status"] == "completed":
            break
        asyncio.run(asyncio.sleep(0.05))
    status = manager.get_background_task_status(task_id)
    assert status["status"] in {"running", "completed"}

    # max background reached branch
    manager.background_tasks["busy"] = {
        "subagent": "explore",
        "task": "x",
        "pid": None,
        "process": type("P", (), {"is_alive": lambda self: True})(),
        "start_time": datetime.now(),
        "status": "running",
    }
    denied = asyncio.run(manager.spawn("explore", "x", background=True))
    assert denied["status"] == "error"

    retry_missing = asyncio.run(manager.retry_background_task("not-found"))
    assert retry_missing["status"] == "error"

    retry_ok = asyncio.run(manager.retry_background_task(task_id))
    assert retry_ok["status"] in {"launched", "error"}


def test_loop_signature_window(monkeypatch, tmp_path):
    _patch_cfg(monkeypatch, tmp_path, enabled=True, loop_window_minutes=1, max_similar_tasks=1)
    manager = mod.SubAgentManager(memory_core=_FakeMemoryCore())
    sig = manager._task_signature("explore", "hello")
    manager.task_history = [{"signature": sig, "timestamp": datetime.now()}]
    assert manager._is_looping("explore", "hello") is True

    # expire history outside window and allow again
    old = datetime.now() - timedelta(minutes=5)
    manager.task_history = [{"signature": sig, "timestamp": old}]
    assert manager._is_looping("explore", "hello") is False
