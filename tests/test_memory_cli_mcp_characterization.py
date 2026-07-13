from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ms8 import cli
from ms8.connect.mcp_server import mcp_server
from ms8.connect.mcp_server.stdio_server import TOOL_DEFINITIONS, handle_request


class _FakeMemoryService:
    def __init__(self) -> None:
        self.submitted: list[dict[str, Any]] = []
        self.query_calls: list[tuple[str, int]] = []
        self.context_calls: list[tuple[str, int]] = []
        self.action_calls: list[dict[str, Any]] = []

    def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        captured = dict(payload)
        self.submitted.append(captured)
        return {
            "ok": True,
            "accepted": True,
            "result": {"status": "saved"},
            "candidate": {
                "source": captured.get("source"),
                "category": captured.get("category", "other"),
            },
        }

    def query(self, text: str, top_k: int = 5) -> dict[str, Any]:
        self.query_calls.append((text, top_k))
        return {
            "ok": True,
            "query": text,
            "count": 1,
            "results": [{"id": "m-query-1", "text": "query result"}],
            "retrieval_gateway": {
                "policy_filter": {
                    "candidate_total": 3,
                    "allowed_total": 1,
                    "blocked_total": 2,
                    "reason_counts": {
                        "low_confidence": 1,
                        "unverified_low_authority": 1,
                    },
                    "low_confidence_refusal": 1,
                }
            },
        }

    def context(self, text: str, limit: int = 5) -> dict[str, Any]:
        self.context_calls.append((text, limit))
        return {
            "ok": True,
            "query": text,
            "context": "stable context",
            "memories": [{"id": "m-context-1", "text": "context memory"}],
            "system_prompt_extra": "Use governed memory only.",
            "recommended_actions": ["submit durable facts after answering"],
        }

    def pre_action_check(
        self,
        action: str,
        *,
        memory_ids: list[str] | None = None,
        explicit_user_confirmation: bool = False,
    ) -> dict[str, Any]:
        call = {
            "action": action,
            "memory_ids": list(memory_ids or []),
            "explicit_user_confirmation": explicit_user_confirmation,
        }
        self.action_calls.append(call)
        allowed = bool(action and memory_ids and explicit_user_confirmation)
        return {
            "ok": True,
            "decision": "allow" if allowed else "deny",
            "allowed": allowed,
            "execution_performed": False,
            **call,
        }


def _install_fake_service(monkeypatch, tmp_path: Path) -> _FakeMemoryService:
    service = _FakeMemoryService()
    connect_root = tmp_path / "connect"
    (connect_root / "logs").mkdir(parents=True)

    monkeypatch.delenv("MS8_CONNECT_CLIENT_TOKEN", raising=False)
    monkeypatch.delenv("MS8_CONNECT_READONLY", raising=False)
    monkeypatch.setattr(mcp_server, "connect_root", lambda: connect_root)
    monkeypatch.setattr(mcp_server, "_load_config", lambda config=None: config or {"mcp": {"enabled": True}})
    monkeypatch.setattr(
        mcp_server.MemoryServiceInterface,
        "from_config",
        classmethod(lambda cls, config: service),
    )
    return service


def test_cli_parser_preserves_ask_and_connect_contracts() -> None:
    parser = cli._build_parser()

    ask = parser.parse_args(["ask", "remember this", "--limit", "7"])
    assert ask.command == "ask"
    assert ask.query == "remember this"
    assert ask.limit == 7

    connect = parser.parse_args(["connect", "status", "--target", "codex"])
    assert connect.command == "connect"
    assert connect.connect_cmd == "status"
    assert connect.target == "codex"


def test_mcp_tool_registry_and_schemas_keep_primary_surfaces() -> None:
    required_tools = {
        "prepare_reply",
        "submit",
        "batch_submit",
        "query",
        "context",
        "pre_action_check",
    }
    assert required_tools.issubset(set(mcp_server.TOOL_NAMES))
    assert required_tools.issubset(set(TOOL_DEFINITIONS))
    assert TOOL_DEFINITIONS["submit"]["inputSchema"]["required"] == ["content"]
    assert TOOL_DEFINITIONS["batch_submit"]["inputSchema"]["required"] == ["memories"]
    assert TOOL_DEFINITIONS["query"]["inputSchema"]["required"] == ["text"]
    assert TOOL_DEFINITIONS["context"]["inputSchema"]["required"] == ["text"]
    assert TOOL_DEFINITIONS["prepare_reply"]["inputSchema"]["required"] == ["text"]
    assert TOOL_DEFINITIONS["pre_action_check"]["inputSchema"]["required"] == ["action"]


def test_submit_and_batch_submit_preserve_source_client_tagging(monkeypatch, tmp_path: Path) -> None:
    service = _install_fake_service(monkeypatch, tmp_path)

    default_submit = mcp_server.call_tool(
        "submit",
        {"content": "default durable memory"},
        config={"mcp": {"enabled": True}},
    )
    explicit_submit = mcp_server.call_tool(
        "submit",
        {"content": "client durable memory", "client": "Codex"},
        config={"mcp": {"enabled": True}},
    )
    preserved_source = mcp_server.call_tool(
        "submit",
        {"content": "explicit source memory", "client": "Codex", "source": "user:manual"},
        config={"mcp": {"enabled": True}},
    )
    batch = mcp_server.call_tool(
        "batch_submit",
        {
            "client_name": "Claude",
            "memories": [
                {"content": "batch durable memory one"},
                {"content": "batch durable memory two", "source": "user:batch"},
            ],
        },
        config={"mcp": {"enabled": True}},
    )

    assert default_submit["ok"] is True
    assert explicit_submit["ok"] is True
    assert preserved_source["ok"] is True
    assert batch["ok"] is True
    assert batch["total"] == 2
    assert batch["accepted"] == 2
    assert batch["rejected"] == 0
    assert [row["source"] for row in service.submitted] == [
        "mcp:submit",
        "mcp:codex",
        "user:manual",
        "mcp:claude",
        "user:batch",
    ]


def test_readonly_mode_blocks_submit_without_calling_service(monkeypatch, tmp_path: Path) -> None:
    service = _install_fake_service(monkeypatch, tmp_path)
    monkeypatch.setenv("MS8_CONNECT_READONLY", "true")

    result = mcp_server.call_tool(
        "submit",
        {"content": "must not be written"},
        config={"mcp": {"enabled": True}},
    )

    assert result == {"ok": False, "error": "readonly_mode", "tool": "submit"}
    assert service.submitted == []


def test_query_and_context_preserve_primary_fields_and_policy_trace(monkeypatch, tmp_path: Path) -> None:
    service = _install_fake_service(monkeypatch, tmp_path)

    query = mcp_server.call_tool(
        "query",
        {"text": "project decision", "top_k": 9},
        config={"mcp": {"enabled": True}},
    )
    context = mcp_server.call_tool(
        "context",
        {"message": "current user turn", "limit": 4},
        config={"mcp": {"enabled": True}},
    )

    assert service.query_calls == [("project decision", 9)]
    assert query["ok"] is True
    assert query["query"] == "project decision"
    assert query["count"] == 1
    assert query["results"][0]["id"] == "m-query-1"
    policy = query["retrieval_gateway"]["policy_filter"]
    assert policy["blocked_total"] == 2
    assert policy["reason_counts"] == {
        "low_confidence": 1,
        "unverified_low_authority": 1,
    }
    assert policy["low_confidence_refusal"] == 1

    assert service.context_calls == [("current user turn", 4)]
    assert context["ok"] is True
    assert context["context"] == "stable context"
    assert context["system_prompt_extra"] == "Use governed memory only."
    assert context["memories"][0]["id"] == "m-context-1"


def test_prepare_reply_adds_workflow_without_replacing_context_fields(monkeypatch, tmp_path: Path) -> None:
    service = _install_fake_service(monkeypatch, tmp_path)

    result = mcp_server.call_tool(
        "prepare_reply",
        {"query": "answer this safely", "limit": 3},
        config={"mcp": {"enabled": True}},
    )

    assert service.context_calls == [("answer this safely", 3)]
    assert result["ok"] is True
    assert result["context"] == "stable context"
    assert result["system_prompt_extra"] == "Use governed memory only."
    assert result["recommended_actions"] == ["submit durable facts after answering"]
    assert result["must_call_before_answer"] is True
    assert result["workflow"] == {
        "step1": "Use context/system_prompt_extra before answering.",
        "step2": "After answering, submit durable facts/preferences/decisions.",
        "step3": "Use batch_submit when multiple durable items exist.",
    }


def test_pre_action_surface_validates_request_and_never_executes(monkeypatch, tmp_path: Path) -> None:
    service = _install_fake_service(monkeypatch, tmp_path)

    invalid = mcp_server.call_tool(
        "pre_action_check",
        {"action": "deploy release", "memory_ids": "m-1"},
        config={"mcp": {"enabled": True}},
    )
    allowed = mcp_server.call_tool(
        "pre_action_check",
        {
            "action": "deploy release",
            "memory_ids": [123, "m-2"],
            "explicit_user_confirmation": True,
        },
        config={"mcp": {"enabled": True}},
    )

    assert invalid == {
        "ok": False,
        "status": "invalid_request",
        "error": "memory_ids_must_be_array",
        "error_code": "E_MCP_INVALID_ACTION_CHECK",
    }
    assert allowed["ok"] is True
    assert allowed["decision"] == "allow"
    assert allowed["allowed"] is True
    assert allowed["execution_performed"] is False
    assert allowed["memory_ids"] == ["123", "m-2"]
    assert service.action_calls == [
        {
            "action": "deploy release",
            "memory_ids": ["123", "m-2"],
            "explicit_user_confirmation": True,
        }
    ]


def test_stdio_tools_list_and_call_wrap_existing_mcp_contract(monkeypatch, tmp_path: Path) -> None:
    _install_fake_service(monkeypatch, tmp_path)

    listed = handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    assert listed is not None
    listed_tools = {item["name"]: item for item in listed["result"]["tools"]}
    assert listed_tools["prepare_reply"]["inputSchema"]["required"] == ["text"]
    assert listed_tools["pre_action_check"]["inputSchema"]["required"] == ["action"]

    called = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "query",
                "arguments": {"text": "stdio query", "top_k": 2},
            },
        }
    )
    assert called is not None
    assert called["result"]["isError"] is False
    payload = json.loads(called["result"]["content"][0]["text"])
    assert payload["ok"] is True
    assert payload["query"] == "stdio query"
    assert payload["retrieval_gateway"]["policy_filter"]["low_confidence_refusal"] == 1
