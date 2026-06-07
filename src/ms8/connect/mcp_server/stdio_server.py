from __future__ import annotations

import json
import logging
import sys
from typing import Any

from ... import __version__

logger = logging.getLogger(__name__)

TOOL_DEFINITIONS: dict[str, dict[str, Any]] = {
    "prepare_reply": {
        "description": (
            "MANDATORY pre-answer step. Call this before every user-facing reply. "
            "It returns memory context + system_prompt_extra + recommended_actions."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Current user message/query."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
            },
            "required": ["text"],
            "additionalProperties": True,
        },
    },
    "context": {
        "description": (
            "Get memory context before answering. Prefer prepare_reply first; if not used, call context "
            "at the start of each user turn before drafting an answer."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Current user message/query."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
            },
            "required": ["text"],
            "additionalProperties": True,
        },
    },
    "submit": {
        "description": (
            "Save one important memory. Call when user states stable preference/decision/constraint/"
            "correction/lesson, or when assistant extracts durable facts from current turn. "
            "Do NOT submit trivial acknowledgements or short/noisy text."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Memory text to persist."},
                "category": {
                    "type": "string",
                    "description": "Memory category.",
                    "enum": ["preference", "decision", "feedback", "pattern", "lesson", "system", "other"],
                },
                "source": {"type": "string", "description": "Source tag, e.g. mcp:claude."},
            },
            "required": ["content"],
            "additionalProperties": True,
        },
    },
    "batch_submit": {
        "description": (
            "Save multiple memories in one call. Use this at end-of-turn/session to persist all high-value facts. "
            "Each item must be durable and non-trivial."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "memories": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 30,
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string"},
                            "category": {
                                "type": "string",
                                "enum": ["preference", "decision", "feedback", "pattern", "lesson", "system", "other"],
                            },
                            "source": {"type": "string"},
                        },
                        "required": ["content"],
                        "additionalProperties": True,
                    },
                }
            },
            "required": ["memories"],
            "additionalProperties": True,
        },
    },
    "query": {
        "description": "Search memories by query text.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 50, "default": 5},
            },
            "required": ["text"],
            "additionalProperties": True,
        },
    },
    "status": {
        "description": "Return lightweight server/runtime status (no heavy checks).",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": True},
    },
    "profile": {
        "description": "Read a profile resource: long-term | profile | recent.",
        "inputSchema": {
            "type": "object",
            "properties": {"key": {"type": "string", "enum": ["long-term", "profile", "recent"]}},
            "additionalProperties": True,
        },
    },
}


def _read_message() -> tuple[dict[str, Any] | None, str]:
    """
    Read one MCP message from stdin.

    Supports both styles:
    - newline-delimited JSON (current MCP stdio spec style used by Claude Desktop)
    - legacy Content-Length framed payloads
    """
    first = sys.stdin.buffer.readline()
    if not first:
        return None, "line"

    stripped = first.strip()
    if stripped.startswith(b"{") or stripped.startswith(b"["):
        try:
            return json.loads(stripped.decode("utf-8")), "line"
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            logger.debug("Failed to decode newline-delimited MCP message: %s", exc)
            return None, "line"

    # Fallback: header-framed mode.
    headers: dict[str, str] = {}
    line = first
    while True:
        if line in (b"\r\n", b"\n"):
            break
        try:
            key, value = line.decode("utf-8").split(":", 1)
            headers[key.strip().lower()] = value.strip()
        except (UnicodeDecodeError, ValueError) as exc:
            print(f"[MCPStdioServer] Ignored malformed header line: {exc}", file=sys.stderr)
        line = sys.stdin.buffer.readline()
        if not line:
            return None, "header"

    content_length = int(headers.get("content-length", "0") or 0)
    if content_length <= 0:
        return None, "header"
    raw = sys.stdin.buffer.read(content_length)
    if not raw:
        return None, "header"
    return json.loads(raw.decode("utf-8")), "header"


def _write_message(payload: dict[str, Any], mode: str = "line") -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if mode == "header":
        head = f"Content-Length: {len(body)}\r\n\r\n".encode()
        sys.stdout.buffer.write(head)
        sys.stdout.buffer.write(body)
    else:
        sys.stdout.buffer.write(body + b"\n")
    sys.stdout.buffer.flush()


def _ok_result(req_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _err_result(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def handle_request(req: dict[str, Any]) -> dict[str, Any] | None:
    method = str(req.get("method") or "")
    req_id = req.get("id")
    params = req.get("params", {}) if isinstance(req.get("params", {}), dict) else {}
    if not method:
        return _err_result(req_id, -32600, "invalid_request")
    if method in {"notifications/initialized", "initialized"}:
        return None
    if method == "initialize":
        client_params = params if isinstance(params, dict) else {}
        protocol_version = str(client_params.get("protocolVersion") or "2025-11-25")
        return _ok_result(
            req_id,
            {
                "protocolVersion": protocol_version,
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"subscribe": False, "listChanged": False},
                },
                "serverInfo": {"name": "ms8-memory", "version": __version__},
            },
        )
    if method == "ping":
        return _ok_result(req_id, {})
    if method == "tools/list":
        from .mcp_server import TOOL_NAMES

        tools = [
            {
                "name": name,
                "description": TOOL_DEFINITIONS.get(name, {}).get("description", f"ms8 memory tool: {name}"),
                "inputSchema": TOOL_DEFINITIONS.get(name, {}).get(
                    "inputSchema", {"type": "object", "properties": {}, "additionalProperties": True}
                ),
            }
            for name in TOOL_NAMES
        ]
        return _ok_result(req_id, {"tools": tools})
    if method == "tools/call":
        from .mcp_server import call_tool

        name = str(params.get("name") or "")
        arguments = params.get("arguments", {}) if isinstance(params.get("arguments", {}), dict) else {}
        out = call_tool(name, arguments)
        text = json.dumps(out, ensure_ascii=False)
        return _ok_result(req_id, {"content": [{"type": "text", "text": text}], "isError": not bool(out.get("ok", False))})
    if method == "resources/list":
        from .mcp_server import RESOURCE_KEYS

        resources = [{"uri": f"ms8://{k}", "name": k, "mimeType": "application/json"} for k in RESOURCE_KEYS]
        return _ok_result(req_id, {"resources": resources})
    if method == "resources/read":
        from .mcp_server import read_resource

        uri = str(params.get("uri") or "")
        key = uri.split("ms8://", 1)[-1] if uri.startswith("ms8://") else uri
        out = read_resource(key)
        text = json.dumps(out, ensure_ascii=False)
        return _ok_result(req_id, {"contents": [{"uri": uri or f"ms8://{key}", "mimeType": "application/json", "text": text}]})
    return _err_result(req_id, -32601, f"method_not_found:{method}")


def main() -> int:
    write_mode = "line"
    while True:
        msg, read_mode = _read_message()
        write_mode = read_mode or write_mode
        if msg is None:
            return 0
        if not isinstance(msg, dict):
            continue
        resp = handle_request(msg)
        if resp is not None:
            _write_message(resp, mode=write_mode)


if __name__ == "__main__":
    raise SystemExit(main())
