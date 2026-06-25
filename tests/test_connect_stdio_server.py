from __future__ import annotations

import io
import json

from ms8.connect.mcp_server import stdio_server


class _FakeStdio:
    def __init__(self, raw: bytes):
        self.buffer = io.BytesIO(raw)


def test_ok_and_err_result_shapes():
    ok = stdio_server._ok_result("id1", {"x": 1})
    err = stdio_server._err_result("id2", -1, "bad")
    assert ok["result"]["x"] == 1
    assert err["error"]["code"] == -1


def test_handle_request_core_methods(monkeypatch):
    init = stdio_server.handle_request(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}}
    )
    assert init is not None
    assert init["result"]["serverInfo"]["name"] == "ms8-memory"

    ping = stdio_server.handle_request({"jsonrpc": "2.0", "id": 2, "method": "ping"})
    assert ping["result"] == {}

    assert stdio_server.handle_request({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert stdio_server.handle_request({"jsonrpc": "2.0", "method": "initialized"}) is None


def test_handle_request_tools_and_resources(monkeypatch):
    import ms8.connect.mcp_server.mcp_server as mcp_mod

    monkeypatch.setattr(mcp_mod, "TOOL_NAMES", ["prepare_reply", "memory_list"])
    monkeypatch.setattr(mcp_mod, "RESOURCE_KEYS", ["profile", "catalog"])
    monkeypatch.setattr(mcp_mod, "call_tool", lambda name, args: {"ok": True, "name": name, "args": args})
    monkeypatch.setattr(mcp_mod, "read_resource", lambda key, params=None: {"ok": True, "key": key, "params": params})

    tools = stdio_server.handle_request({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
    assert tools is not None
    assert tools["result"]["tools"][0]["name"] == "prepare_reply"
    assert tools["result"]["tools"][1]["name"] == "memory_list"

    called = stdio_server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "prepare_reply", "arguments": {"text": "hi"}},
        }
    )
    assert called is not None
    body = json.loads(called["result"]["content"][0]["text"])
    assert body["ok"] is True
    assert body["name"] == "prepare_reply"

    resources = stdio_server.handle_request({"jsonrpc": "2.0", "id": 5, "method": "resources/list"})
    assert resources["result"]["resources"][0]["uri"] == "ms8://profile"
    assert resources["result"]["resources"][1]["uri"] == "ms8://catalog"

    read = stdio_server.handle_request(
        {"jsonrpc": "2.0", "id": 6, "method": "resources/read", "params": {"uri": "ms8://profile"}}
    )
    payload = json.loads(read["result"]["contents"][0]["text"])
    assert payload["ok"] is True
    assert payload["key"] == "profile"


def test_handle_request_invalid_and_unknown():
    invalid = stdio_server.handle_request({"jsonrpc": "2.0", "id": 7})
    assert invalid["error"]["code"] == -32600

    unknown = stdio_server.handle_request({"jsonrpc": "2.0", "id": 8, "method": "nope"})
    assert unknown["error"]["code"] == -32601


def test_read_message_line_mode(monkeypatch):
    msg = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    raw = (json.dumps(msg) + "\n").encode("utf-8")
    monkeypatch.setattr(stdio_server.sys, "stdin", _FakeStdio(raw))
    parsed, mode = stdio_server._read_message()
    assert mode == "line"
    assert parsed == msg


def test_read_message_header_mode(monkeypatch):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode("utf-8")
    raw = b"Content-Length: " + str(len(body)).encode("utf-8") + b"\r\n\r\n" + body
    monkeypatch.setattr(stdio_server.sys, "stdin", _FakeStdio(raw))
    parsed, mode = stdio_server._read_message()
    assert mode == "header"
    assert parsed["method"] == "ping"


def test_read_message_bad_line_and_empty(monkeypatch):
    monkeypatch.setattr(stdio_server.sys, "stdin", _FakeStdio(b"not-json\n"))
    parsed, mode = stdio_server._read_message()
    assert parsed is None
    assert mode == "header"

    monkeypatch.setattr(stdio_server.sys, "stdin", _FakeStdio(b""))
    parsed2, _mode2 = stdio_server._read_message()
    assert parsed2 is None
