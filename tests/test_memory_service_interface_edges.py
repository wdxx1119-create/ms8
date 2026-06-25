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


class _EngineAdapterMinimal:
    def __init__(self, rows=None):
        self.rows = rows if rows is not None else []

    def read_memories(self):
        return list(self.rows)

    def retrieve_gateway(self, query: str, limit: int = 5, purpose: str = "recall", allow_semantic: bool = False, allow_graph: bool = False):
        return {"items": list(self.rows)[:limit], "trace": {"query": query, "purpose": purpose}}


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


def test_memory_catalog_list_get_and_search() -> None:
    rows = [
        {
            "id": "m1",
            "text": "alpha memory",
            "source": "ask",
            "category": "decision",
            "status": "accepted",
            "created_at": "2026-01-01T00:00:00Z",
            "extra": {"k": "v"},
        },
        {
            "id": "m2",
            "text": "beta memory",
            "source": "mcp:submit",
            "category": "preference",
            "status": "accepted",
            "created_at": "2026-01-02T00:00:00Z",
        },
    ]
    svc = MemoryServiceInterface(config={}, core=_CoreMinimal())
    svc._engine_adapter = lambda: _EngineAdapterMinimal(rows)  # type: ignore[method-assign]

    catalog = svc.memory_catalog()
    assert catalog["ok"] is True
    assert catalog["total"] == 2
    assert catalog["sources"]["ask"] == 1

    listed = svc.memory_list(limit=1, view="summary", source="ask")
    assert listed["ok"] is True
    assert listed["total"] == 1
    assert listed["items"][0]["id"] == "m1"

    full = svc.memory_get("m1", view="full")
    assert full["ok"] is True
    assert full["item"]["extra"]["k"] == "v"

    missing = svc.memory_get("missing")
    assert missing["ok"] is False
    assert missing["status"] == "not_found"

    search = svc.memory_search("alpha", limit=5, view="summary")
    assert search["ok"] is True
    assert search["items"][0]["id"] == "m1"


def test_memory_query_validation_errors() -> None:
    svc = MemoryServiceInterface(config={}, core=_CoreMinimal())
    svc._engine_adapter = lambda: _EngineAdapterMinimal([])  # type: ignore[method-assign]

    invalid_get = svc.memory_get("")
    assert invalid_get["ok"] is False
    assert invalid_get["status"] == "invalid_request"

    invalid_search = svc.memory_search("")
    assert invalid_search["ok"] is False
    assert invalid_search["status"] == "invalid_request"

    try:
        svc.memory_list(limit=501)
    except ValueError as exc:
        assert "limit must be between 1 and 500" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("memory_list should reject an oversized limit")


def test_read_only_memory_tools_work_without_core() -> None:
    rows = [{"id": "m1", "text": "alpha", "source": "ask", "category": "decision", "status": "accepted", "created_at": "t"}]
    svc = MemoryServiceInterface(config={}, core=None, core_error="core init failed")
    svc._engine_adapter = lambda: _EngineAdapterMinimal(rows)  # type: ignore[method-assign]

    assert svc.memory_catalog()["ok"] is True
    assert svc.memory_list()["ok"] is True
    assert svc.memory_get("m1")["ok"] is True
    assert svc.memory_search("alpha")["ok"] is True



def test_memory_default_visibility_and_explicit_audit_redaction(tmp_path: Path) -> None:
    records = tmp_path / "memory" / "auto_memory_records.jsonl"
    records.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"id": "ok", "text": "visible", "normalized_text": "visible", "status": "accepted", "source": "ask", "category": "general", "created_at": "2026-01-01T00:00:00Z", "scope": "personal", "authority": "user_explicit", "sensitivity": "private", "can_recall": True},
        {"id": "candidate", "text": "candidate", "normalized_text": "candidate", "status": "candidate", "source": "ask", "category": "general", "created_at": "2026-01-02T00:00:00Z", "scope": "personal", "authority": "user_explicit", "sensitivity": "private", "can_recall": True},
        {"id": "secret", "text": "password=abc", "normalized_text": "password=abc", "status": "accepted", "source": "ask", "category": "general", "created_at": "2026-01-03T00:00:00Z", "scope": "personal", "authority": "user_explicit", "sensitivity": "secret", "can_recall": True, "token": "abc"},
        {"id": "disabled", "text": "disabled", "normalized_text": "disabled", "status": "accepted", "source": "ask", "category": "general", "created_at": "2026-01-04T00:00:00Z", "scope": "personal", "authority": "user_explicit", "sensitivity": "private", "can_recall": False},
    ]
    records.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    class _AuditAdapter(_EngineAdapterMinimal):
        def records_file(self):
            return records

        def read_memories(self):
            return [row for row in rows if row.get("can_recall", True)]

    svc = MemoryServiceInterface(config={}, core=_CoreMinimal())
    svc._engine_adapter = lambda: _AuditAdapter(rows)  # type: ignore[method-assign]

    default_list = svc.memory_list(view="full")
    assert [item["id"] for item in default_list["items"]] == ["ok"]
    assert svc.memory_get("candidate")["status"] == "not_found"

    audit_list = svc.memory_list(view="full", include_blocked=True)
    assert audit_list["audit_view"] is True
    assert audit_list["total"] == 4
    secret = next(item for item in audit_list["items"] if item["id"] == "secret")
    assert secret["text"] == "[REDACTED]"
    assert secret["token"] == "[REDACTED]"
