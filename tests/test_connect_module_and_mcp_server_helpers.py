from __future__ import annotations

from pathlib import Path

import pytest

import ms8.connect as connect_mod
from ms8.connect.mcp_server import mcp_server as server


def test_connect_module_getattr_and_errors() -> None:
    cls = connect_mod.MemoryServiceInterface
    assert cls is not None
    with pytest.raises(AttributeError):
        getattr(connect_mod, "NotExists")


def test_mcp_server_helper_basics(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MS8_CONNECT_READONLY", "true")
    assert server._write_allowed() is False
    monkeypatch.setenv("MS8_CONNECT_READONLY", "false")
    assert server._write_allowed() is True

    assert server._normalize_text("  A   B  ") == "a b"
    assert len(server._text_hash("abc")) == 40
    assert server._source_tag("Submit") == "mcp:submit"
    assert server._get_client_name({"client_name": "x"}) == "x"

    ok, reason = server._is_low_value_text("ok")
    assert ok is True and reason
    ok2, _ = server._is_low_value_text("this is useful enough")
    assert ok2 is False

    guard_path = tmp_path / "runtime" / "submit_guard_state.json"
    monkeypatch.setattr(server, "_guard_state_path", lambda: guard_path)
    payload = {"content": "valuable memory content"}
    accepted1, reason1 = server._guard_admission(payload)
    assert accepted1 is True and reason1 == ""
    accepted2, reason2 = server._guard_admission(payload)
    assert accepted2 is False and reason2 == "cooldown_duplicate"


def test_mcp_server_tool_paths_and_auth(monkeypatch) -> None:
    class _Svc:
        @classmethod
        def from_config(cls, _cfg):
            return cls()

        def status(self):
            return {"ok": True}

        def quick_status(self):
            return {"ok": True, "status": "ok"}

        def context(self, text, limit):
            return {"ok": True, "text": text, "limit": limit}

        def submit(self, payload):
            return {"ok": True, "payload": payload}

        def query(self, text, top_k):
            return {"ok": True, "text": text, "top_k": top_k}

        def profile(self, key):
            return {"ok": True, "key": key}

        def memory_catalog(self):
            return {"ok": True, "total": 1}

        def memory_list(self, **kwargs):
            return {"ok": True, "kwargs": kwargs}

        def memory_get(self, memory_id, view="full"):
            return {"ok": True, "id": memory_id, "view": view}

        def memory_search(self, query, limit=20, view="summary"):
            return {"ok": True, "query": query, "limit": limit, "view": view}

    monkeypatch.setattr(server, "MemoryServiceInterface", _Svc)
    monkeypatch.setattr(server, "_load_registry", lambda: {"a": 1})
    monkeypatch.setattr(server, "_audit", lambda *_a, **_k: None)
    monkeypatch.setattr(server, "_guard_admission", lambda _p: (True, ""))
    monkeypatch.setattr(server, "_load_config", lambda _cfg=None: {})
    monkeypatch.setenv("MS8_CONNECT_CLIENT_TOKEN", "tok")

    denied = server.call_tool("status", {"token": "bad", "client": "c1"})
    assert denied["ok"] is False
    assert denied["error"] == "invalid_client_token"

    allowed = server.call_tool("prepare_reply", {"token": "tok", "text": "hi", "limit": 2})
    assert allowed["ok"] is True
    assert allowed["must_call_before_answer"] is True

    submit_out = server.call_tool("submit", {"token": "tok", "content": "hello"})
    assert submit_out["ok"] is True

    batch_bad = server.call_tool("batch_submit", {"token": "tok", "memories": []})
    assert batch_bad["ok"] is False
    batch_ok = server.call_tool("batch_submit", {"token": "tok", "memories": [{"content": "a"}, {"content": "b"}]})
    assert batch_ok["ok"] is True
    assert batch_ok["accepted"] == 2

    assert server.call_tool("query", {"token": "tok", "text": "x"})["ok"] is True
    assert server.call_tool("context", {"token": "tok", "text": "x"})["ok"] is True
    assert server.call_tool("status", {"token": "tok"})["ok"] is True
    assert server.call_tool("profile", {"token": "tok", "key": "profile"})["ok"] is True
    assert server.call_tool("memory_catalog", {"token": "tok"})["ok"] is True
    assert server.call_tool("memory_list", {"token": "tok", "limit": 2})["ok"] is True
    assert server.call_tool("memory_get", {"token": "tok", "id": "m1"})["ok"] is True
    assert server.call_tool("memory_search", {"token": "tok", "text": "x"})["ok"] is True
    assert server.call_tool("unknown", {"token": "tok"})["ok"] is False

    created = server.create_server({})
    assert created["ok"] is True
    assert created["registry_count"] == 1
    assert "prepare_reply" in created["tools"]


def test_mcp_server_readonly_and_read_resource(monkeypatch) -> None:
    class _Svc:
        @classmethod
        def from_config(cls, _cfg):
            return cls()

        def profile(self, key):
            return {"ok": True, "key": key}

        def memory_catalog(self):
            return {"ok": True, "total": 0}

        def memory_get(self, memory_id, view="full"):
            return {"ok": True, "id": memory_id, "view": view}

    monkeypatch.setattr(server, "MemoryServiceInterface", _Svc)
    monkeypatch.setattr(server, "_audit", lambda *_a, **_k: None)
    monkeypatch.setattr(server, "_load_config", lambda _cfg=None: {})
    monkeypatch.setattr(server, "_write_allowed", lambda: False)
    monkeypatch.setenv("MS8_CONNECT_CLIENT_TOKEN", "")

    out = server.call_tool("submit", {"content": "x"})
    assert out["ok"] is False
    assert out["error"] == "readonly_mode"

    batch = server.call_tool("batch_submit", {"memories": [{"content": "x"}]})
    assert batch["ok"] is False
    assert batch["error"] == "readonly_mode"

    prof = server.read_resource("profile", {})
    assert prof["ok"] is True
    assert prof["key"] == "profile"

    catalog = server.read_resource("catalog", {})
    assert catalog["ok"] is True

    memory = server.read_resource("memory/m1", {})
    assert memory["ok"] is True
    assert memory["id"] == "m1"
