from __future__ import annotations

import asyncio
import builtins
import collections
from types import SimpleNamespace
from typing import Any

from ms8.engine_core import core as core_mod
from ms8.engine_core.core import MemoryCore


def _core_stub() -> MemoryCore:
    c = MemoryCore.__new__(MemoryCore)
    c._recent_query_tokens = collections.deque(maxlen=24)
    c.config = {
        "workspace_dir": ".",
        "memory_dir": ".",
        "settings": {"memory": {"self_check": {"allow_r3_auto_apply": False}}},
    }
    c.crypto = SimpleNamespace(is_enabled=lambda: False, is_unlocked=lambda: True)
    return c


def test_get_self_check_report_and_self_repair_report_error_wrappers(monkeypatch) -> None:
    c = _core_stub()

    monkeypatch.setattr(core_mod, "load_latest_report", lambda _cfg: (_ for _ in ()).throw(RuntimeError("sc")))
    monkeypatch.setattr(
        core_mod, "load_latest_repair_report", lambda _mem_dir: (_ for _ in ()).throw(RuntimeError("sr"))
    )

    out_sc = c.get_self_check_report()
    out_sr = c.get_self_repair_report()
    assert out_sc["status"] == "error"
    assert "sc" in out_sc["error"]
    assert out_sr["status"] == "error"
    assert "sr" in out_sr["error"]


def test_run_self_repair_exception_wrapper(monkeypatch) -> None:
    c = _core_stub()
    monkeypatch.setattr(
        core_mod, "build_self_repair_plan", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("plan boom"))
    )
    out = c.run_self_repair(mode="apply", risk="R1")
    assert out["status"] == "error"
    assert out["mode"] == "apply"


def test_evaluate_admission_fallback_when_import_fails(monkeypatch) -> None:
    c = _core_stub()

    real_import = builtins.__import__

    def _bad_import(name, *args, **kwargs):  # noqa: ANN001
        if name == ".admission_compat":
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _bad_import)
    out = c._evaluate_admission("  hello  ", source="x")
    assert out["normalized_text"] == "hello"
    assert out["route"] == "accepted"
    assert out["should_persist_main"] is True


def test_run_async_running_loop_path(monkeypatch) -> None:
    c = _core_stub()

    async def _coro() -> int:
        return 11

    monkeypatch.setattr(asyncio, "get_running_loop", lambda: object())
    out = c._run_async(_coro())
    assert out == 11


def test_run_async_thread_runtime_error_raises(monkeypatch) -> None:
    c = _core_stub()

    async def _coro() -> int:
        return 12

    monkeypatch.setattr(asyncio, "get_running_loop", lambda: object())

    def _boom_run(_value: Any) -> Any:
        raise RuntimeError("loop-broken")

    monkeypatch.setattr(asyncio, "run", _boom_run)
    coro = _coro()
    try:
        c._run_async(coro)
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "loop-broken" in str(exc)
    finally:
        try:
            coro.close()
        except RuntimeError:
            pass


def test_write_gateway_uses_core_admission(monkeypatch, tmp_path) -> None:
    c = _core_stub()
    c.config["memory_dir"] = str(tmp_path)
    c.file_store = SimpleNamespace(append_to_daily_log=lambda value: None)

    monkeypatch.setattr(
        c,
        "_evaluate_admission",
        lambda text, source="x": {
            "route": "redacted_accept",
            "reasons": ["privacy_hit"],
            "normalized_text": "[REDACTED]",
            "should_persist_main": True,
        },
    )

    captured: dict[str, str] = {}

    def _fake_append_memory_record(*, memory_dir, text, source, status="accepted"):  # noqa: ANN001
        captured["text"] = text
        captured["source"] = source
        return {"id": "rec-1"}

    monkeypatch.setattr(core_mod, "append_memory_record", _fake_append_memory_record)
    out = c.write_gateway("secret payload", source="unit", category="general")
    assert out["status"] == "accepted"
    assert out["admission"]["route"] == "redacted_accept"
    assert captured["text"] == "[REDACTED]"


def test_write_gateway_rejects_when_core_admission_blocks(monkeypatch, tmp_path) -> None:
    c = _core_stub()
    c.config["memory_dir"] = str(tmp_path)

    monkeypatch.setattr(
        c,
        "_evaluate_admission",
        lambda text, source="x": {
            "route": "rejected",
            "reasons": ["blocked"],
            "normalized_text": text,
            "should_persist_main": False,
        },
    )

    out = c.write_gateway("blocked payload", source="unit", category="general")
    assert out["status"] == "rejected"
    assert out["admission"]["route"] == "rejected"


def test_write_gateway_uses_provided_admission_without_re_evaluating(monkeypatch, tmp_path) -> None:
    c = _core_stub()
    c.config["memory_dir"] = str(tmp_path)
    c.file_store = SimpleNamespace(append_to_daily_log=lambda value: None)

    monkeypatch.setattr(c, "_evaluate_admission", lambda text, source="x": (_ for _ in ()).throw(RuntimeError("should not run")))

    captured: dict[str, str] = {}

    def _fake_append_memory_record(*, memory_dir, text, source, status="accepted"):  # noqa: ANN001
        captured["text"] = text
        return {"id": "rec-2"}

    monkeypatch.setattr(core_mod, "append_memory_record", _fake_append_memory_record)
    out = c.write_gateway(
        "raw payload",
        source="unit",
        category="general",
        admission={"route": "accepted", "normalized_text": "provided-normalized", "should_persist_main": True},
    )
    assert out["status"] == "accepted"
    assert captured["text"] == "provided-normalized"


def test_core_save_uses_gateway_normalized_text(monkeypatch, tmp_path) -> None:
    c = _core_stub()
    c.config["memory_dir"] = str(tmp_path)
    writes: list[str] = []
    entity_hits: list[str] = []
    kg_hits: list[str] = []
    c.file_store = SimpleNamespace(read_memory_md=lambda: "ROOT", write_memory_md=lambda value: writes.append(value))
    c.get_memory_blocks = lambda: {}  # type: ignore[method-assign]
    c.governance = SimpleNamespace(assess_memory_write=lambda text, blocks, files: {"is_duplicate": False, "text": text})
    c._extract_and_store_entities = lambda text: entity_hits.append(text)  # type: ignore[method-assign]
    c.whoosh_search = SimpleNamespace(reindex_all=lambda: None)
    c._dispatch_knowledge_graph_ingest = lambda path, key, text, use_llm=True: kg_hits.append(text)  # type: ignore[method-assign]
    c.maintenance = SimpleNamespace(run_maintenance=lambda force=False: None)
    c._mark_write_success = lambda source: None  # type: ignore[method-assign]
    c._run_maintenance_policy = lambda force=False: None  # type: ignore[method-assign]
    c._maybe_generate_synthetic_candidates = lambda: {"status": "ok"}  # type: ignore[method-assign]
    c.monitoring = SimpleNamespace(status=lambda: None)
    c.git_manager = SimpleNamespace(commit_if_needed=lambda: None)
    c.shadow = None
    c.config["settings"] = {"memory": {"git": {"auto_commit": False}}}
    c._safe_text_for_memory_md = lambda text: {"allowed": True, "text": text, "admission": {"normalized_text": "safe-admission"}}  # type: ignore[method-assign]  # noqa: E501
    c.write_gateway = lambda text, source, category, write_daily_log=False, admission=None: {  # type: ignore[method-assign]
        "status": "accepted",
        "record_id": "rec-1",
        "admission": {"normalized_text": "gateway-normalized"},
    }

    c.save("MyKey", "raw-input")
    assert writes[-1].endswith("## MyKey\ngateway-normalized")
    assert entity_hits == ["gateway-normalized"]
    assert kg_hits == ["gateway-normalized"]
