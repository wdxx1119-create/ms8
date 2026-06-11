from __future__ import annotations

import json
from pathlib import Path

from ms8.connect.mcp_server.memory_service_interface import MemoryServiceInterface


class _CoreMinimal:
    def write_gateway(self, content: str, source: str, category: str, write_daily_log: bool = True):
        return {"status": "saved", "content": content, "source": source, "category": category}

    def retrieve_memories(self, query: str, top_k: int = 5):
        return [{"id": "1", "text": query, "source": "u", "category": "c", "status": "accepted", "created_at": "t"}]

    def get_response_memory_context(self, query: str):
        return {"context": f"ctx:{query}", "memories": [{"text": query}]}

    def get_monitoring_status(self):
        return {"ok": True}


def test_from_config_fallback_workspace(monkeypatch, tmp_path: Path) -> None:
    # Force workspace fallback path branch.
    monkeypatch.setattr(MemoryServiceInterface, "_is_writable_dir", staticmethod(lambda _p: False))

    class _FakeCore:
        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setattr("ms8.connect.mcp_server.memory_service_interface.MemoryCore", _FakeCore)
    monkeypatch.chdir(tmp_path)
    svc = MemoryServiceInterface.from_config({"memory_core": {"workspace": str(tmp_path / "x")}})
    assert isinstance(svc, MemoryServiceInterface)
    assert svc.core is not None


def test_from_config_core_init_failure(monkeypatch, tmp_path: Path) -> None:
    def _boom(*args, **kwargs):
        raise RuntimeError("core init failed")

    monkeypatch.setattr("ms8.connect.mcp_server.memory_service_interface.MemoryCore", _boom)
    svc = MemoryServiceInterface.from_config({"memory_core": {"workspace": str(tmp_path)}})
    assert svc.core is None
    assert "core init failed" in svc.core_error
    out = svc.quick_status()
    assert out["ok"] is False


def test_submit_empty_and_submit_exception() -> None:
    svc = MemoryServiceInterface(config={}, core=_CoreMinimal())
    empty = svc.submit({"content": ""})
    assert empty["ok"] is False
    assert empty["error"] == "empty_content"

    class _CoreBoom(_CoreMinimal):
        def write_gateway(self, *args, **kwargs):
            raise ValueError("bad write")

    svc_boom = MemoryServiceInterface(config={}, core=_CoreBoom())
    out = svc_boom.submit({"content": "x"})
    assert out["ok"] is False
    assert out["error_code"] == "E_MCP_SUBMIT_FAILED"


def test_query_and_context_exception_paths() -> None:
    qsvc = MemoryServiceInterface(config={}, core=_CoreMinimal())
    qsvc._engine_adapter = lambda: (_ for _ in ()).throw(RuntimeError("query boom"))  # type: ignore[method-assign]
    qout = qsvc.query("abc", 2)
    assert qout["ok"] is False
    assert qout["error_code"] == "E_MCP_QUERY_FAILED"

    class _CoreCtxBoom(_CoreMinimal):
        def get_response_memory_context(self, query: str):
            raise RuntimeError("ctx boom")

    csvc = MemoryServiceInterface(config={}, core=_CoreCtxBoom())
    cout = csvc.context("abc", 2)
    assert cout["ok"] is False
    assert cout["error_code"] == "E_MCP_CONTEXT_FAILED"


def test_status_typeerror_fallback() -> None:
    class _CoreStatusTypeError(_CoreMinimal):
        def get_monitoring_status(self, lightweight: bool = False):
            if lightweight:
                raise TypeError("no lightweight")
            return {"ok": True, "mode": "fallback"}

    svc = MemoryServiceInterface(config={}, core=_CoreStatusTypeError())
    out = svc.status()
    assert out["ok"] is True
    assert out["health"]["mode"] == "fallback"


def test_profile_recent_profile_and_oserror_paths(tmp_path: Path) -> None:
    ws = tmp_path
    mem = ws / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    records = mem / "auto_memory_records.jsonl"
    records.write_text('{"id":"1","text":"ok"}\n{\n{"id":"2","text":"x"}\n', encoding="utf-8")
    svc = MemoryServiceInterface(config={"memory_core": {"workspace": str(ws)}}, core=_CoreMinimal())
    recent = svc.profile("recent")
    assert recent["ok"] is True
    # malformed line is skipped
    assert isinstance(recent["content"], list)
    assert len(recent["content"]) == 2

    blocks = mem / "memory_blocks.json"
    blocks.write_text(json.dumps({"a": 1}), encoding="utf-8")
    prof = svc.profile("profile")
    assert prof["ok"] is True

    # OSError path: monkeypatch _workspace to non-readable file as MEMORY.md
    bad_ws = tmp_path / "bad"
    bad_ws.mkdir()
    (bad_ws / "MEMORY.md").mkdir()  # directory -> read_text raises IsADirectoryError (OSError)
    svc_bad = MemoryServiceInterface(config={"memory_core": {"workspace": str(bad_ws)}}, core=_CoreMinimal())
    out_bad = svc_bad.profile("long-term")
    assert out_bad["ok"] is False


def test_private_helpers_resource_int_and_normalizers() -> None:
    svc = MemoryServiceInterface(config={"resources": {"x": "7", "y": "bad"}}, core=_CoreMinimal())
    assert svc._resource_int("x", 1) == 7
    assert svc._resource_int("y", 2) == 2
    assert svc._resource_int("z", 3) == 3

    normalized = svc._normalize_submit_result(
        [
            {"status": "saved"},
            {"status": "pending_review"},
            {"status": "rejected"},
            {"result": {"status": "accepted"}},
            "bad",
        ]
    )
    assert normalized["saved_any"] is True
    assert normalized["accepted_count"] == 2
    assert normalized["review_count"] == 1
    assert normalized["rejected_count"] >= 1

    safe = svc._json_safe({"a": {1, 2}})
    assert isinstance(safe["a"], list)


def test_build_expression_context_router_failure_fallback(monkeypatch, tmp_path: Path) -> None:
    svc = MemoryServiceInterface(config={"memory_core": {"workspace": str(tmp_path)}}, core=_CoreMinimal())
    monkeypatch.setattr(
        "ms8.connect.mcp_server.memory_service_interface.load_conversation_state",
        lambda _d: (_ for _ in ()).throw(RuntimeError("state read failed")),
    )
    out = svc._build_expression_context("hello", {"context": "x"})
    assert out["mode"] == "normal"
    assert "router_fallback_normal" in out["decision"]["reason"]
