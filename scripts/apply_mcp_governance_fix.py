from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def regex_once(text: str, pattern: str, replacement: str, label: str) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.DOTALL)
    if count != 1:
        raise RuntimeError(f"{label}: expected one regex match, found {count}")
    return updated


POLICY_MODULE = '''from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

BROWSABLE_STATUSES = {"short_term", "accepted", "verified"}
BLOCKED_SENSITIVITIES = {"secret", "credential"}
UNTRUSTED_AUTHORITIES = {"assistant_inferred", "tool_generated"}
BLOCKED_SCOPES = {"labs", "system_debug"}
SENSITIVE_KEYS = {
    "api_key",
    "authorization",
    "credential",
    "credentials",
    "password",
    "refresh_token",
    "secret",
    "token",
    "access_token",
}


def _is_expired(row: dict[str, Any]) -> bool:
    valid_until = str(row.get("valid_until") or row.get("ttl") or "").strip()
    if not valid_until:
        return False
    try:
        raw = valid_until[:-1] + "+00:00" if valid_until.endswith("Z") else valid_until
        value = datetime.fromisoformat(raw)
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc) < datetime.now(timezone.utc)
    except ValueError:
        return True


def memory_row_browsable(row: dict[str, Any]) -> bool:
    """Return whether a record is safe for default explicit MCP browsing."""

    status = str(row.get("status", "")).strip().lower()
    if status not in BROWSABLE_STATUSES:
        return False
    if row.get("can_recall", True) is False:
        return False
    if str(row.get("superseded_by", "")).strip():
        return False
    if _is_expired(row):
        return False
    sensitivity = str(row.get("sensitivity", "private")).strip().lower()
    if sensitivity in BLOCKED_SENSITIVITIES:
        return False
    authority = str(row.get("authority", "user_implicit")).strip().lower()
    if authority in UNTRUSTED_AUTHORITIES and status != "verified":
        return False
    scope = str(row.get("scope", "")).strip().lower()
    if scope in BLOCKED_SCOPES:
        return False
    return True


def redact_memory_row(row: dict[str, Any]) -> dict[str, Any]:
    """Return a defensive copy with credentials and secret payloads removed."""

    sensitivity = str(row.get("sensitivity", "private")).strip().lower()

    def redact(value: Any, key: str = "") -> Any:
        normalized_key = key.strip().lower()
        if normalized_key in SENSITIVE_KEYS:
            return "[REDACTED]"
        if isinstance(value, dict):
            return {str(k): redact(v, str(k)) for k, v in value.items()}
        if isinstance(value, list):
            return [redact(item) for item in value]
        if isinstance(value, tuple):
            return [redact(item) for item in value]
        return value

    safe = redact(dict(row))
    if not isinstance(safe, dict):
        return {"redacted": True}
    if sensitivity in BLOCKED_SENSITIVITIES:
        for field in ("text", "normalized_text", "content", "value"):
            if field in safe:
                safe[field] = "[REDACTED]"
        safe["redacted"] = True
        safe["redaction_reason"] = f"sensitivity:{sensitivity}"
    return safe
'''


def patch_memory_service() -> None:
    path = ROOT / "src/ms8/connect/mcp_server/memory_service_interface.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "from ..integration_hooks.service_models import MemoryCandidate\n",
        "from ..integration_hooks.service_models import MemoryCandidate\n"
        "from .memory_access_policy import memory_row_browsable, redact_memory_row\n",
        "memory service import",
    )

    methods = '''
    def memory_catalog(self, *, include_blocked: bool = False) -> dict[str, Any]:
        rows = self._read_memory_rows(include_blocked=include_blocked)
        latest_created_at = max((str(row.get("created_at", "")) for row in rows), default="")
        return {
            "ok": True,
            "provider": "ms8_runtime",
            "read_only": True,
            "audit_view": bool(include_blocked),
            "total": len(rows),
            "sources": dict(sorted(Counter(str(row.get("source", "")) for row in rows if row.get("source")).items())),
            "categories": dict(
                sorted(Counter(str(row.get("category", "")) for row in rows if row.get("category")).items())
            ),
            "statuses": dict(sorted(Counter(str(row.get("status", "")) for row in rows if row.get("status")).items())),
            "latest_created_at": latest_created_at,
        }

    def memory_list(
        self,
        *,
        offset: int = 0,
        limit: int = DEFAULT_PAGE_SIZE,
        view: str = "summary",
        source: str = "",
        category: str = "",
        status: str = "",
        include_blocked: bool = False,
    ) -> dict[str, Any]:
        offset, limit = self._validated_page(offset, limit)
        rows = self._filter_memory_rows(
            self._read_memory_rows(include_blocked=include_blocked),
            source=source,
            category=category,
            status=status,
        )
        page = rows[offset : offset + limit]
        next_offset = offset + len(page)
        return {
            "ok": True,
            "provider": "ms8_runtime",
            "audit_view": bool(include_blocked),
            "view": view,
            "offset": offset,
            "limit": limit,
            "total": len(rows),
            "next_offset": next_offset if next_offset < len(rows) else None,
            "items": [self._render_memory_row(row, view) for row in page],
        }

    def memory_get(
        self,
        memory_id: str,
        *,
        view: str = "full",
        include_blocked: bool = False,
    ) -> dict[str, Any]:
        normalized_id = str(memory_id or "").strip()
        if not normalized_id:
            return {"ok": False, "status": "invalid_request", "reason": "memory_id_required"}
        for row in self._read_memory_rows(include_blocked=include_blocked):
            if str(row.get("id", "")).strip() == normalized_id:
                return {
                    "ok": True,
                    "provider": "ms8_runtime",
                    "audit_view": bool(include_blocked),
                    "item": self._render_memory_row(row, view),
                }
        return {
            "ok": False,
            "status": "not_found",
            "reason": "memory_not_found",
            "memory_id": normalized_id,
        }

    def memory_search(self, query: str, *, limit: int = 20, view: str = "summary") -> dict[str, Any]:
        text = str(query or "").strip()
        if not text:
            return {"ok": False, "status": "invalid_request", "reason": "query_required", "items": []}
        try:
            gateway = self._engine_adapter().retrieve_gateway(
                query=text,
                limit=int(max(1, min(limit, MAX_PAGE_SIZE))),
                purpose="recall",
                allow_semantic=False,
                allow_graph=False,
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning("mcp_memory_search_failed query=%s err=%s", text, exc)
            return {
                "ok": False,
                "status": "error",
                "reason": str(exc),
                "error_code": "E_MCP_MEMORY_SEARCH_FAILED",
                "items": [],
            }
        rows = gateway.get("items", []) if isinstance(gateway.get("items", []), list) else []
        items = [self._render_memory_row(row, view) for row in rows if isinstance(row, dict)]
        return {
            "ok": True,
            "provider": "ms8_runtime",
            "query": text,
            "limit": int(max(1, min(limit, MAX_PAGE_SIZE))),
            "total_matches": len(items),
            "items": items,
            "retrieval_gateway": gateway.get("trace", {}),
        }
'''
    text = regex_once(
        text,
        r"\n    def memory_catalog\(.*?\n    def context\(",
        methods + "\n    def context(",
        "memory service methods",
    )

    readers = '''
    @staticmethod
    def _read_all_memory_rows(adapter: MemoryCoreEngine) -> list[dict[str, Any]]:
        records_file = adapter.records_file()
        if not records_file.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in records_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                logger.debug("skip_invalid_mcp_memory_json err=%s", exc)
                continue
            if isinstance(payload, dict):
                if "text" not in payload and "normalized_text" in payload:
                    payload["text"] = payload.get("normalized_text", "")
                rows.append(payload)
        return rows

    def _read_memory_rows(self, *, include_blocked: bool = False) -> list[dict[str, Any]]:
        adapter = self._engine_adapter()
        source_rows = self._read_all_memory_rows(adapter) if include_blocked else adapter.read_memories()
        rows = [dict(row) for row in source_rows if isinstance(row, dict)]
        if include_blocked:
            return rows
        return [row for row in rows if memory_row_browsable(row)]

    @staticmethod
    def _filter_memory_rows'''
    text = regex_once(
        text,
        r"\n    def _read_memory_rows\(self\).*?\n    @staticmethod\n    def _filter_memory_rows",
        "\n" + readers,
        "memory row readers",
    )

    renderer = '''
    def _render_memory_row(self, row: dict[str, Any], view: str) -> dict[str, Any]:
        safe_row = redact_memory_row(row)
        if view == "summary":
            return self._normalize_result_row(safe_row)
        if view == "full":
            return self._json_safe(safe_row)
        raise ValueError("view must be 'summary' or 'full'")

    def _normalize_submit_result'''
    text = regex_once(
        text,
        r"\n    def _render_memory_row\(self, row: dict\[str, Any\], view: str\).*?\n    def _normalize_submit_result",
        "\n" + renderer,
        "memory renderer",
    )
    path.write_text(text, encoding="utf-8")


def patch_mcp_server() -> None:
    path = ROOT / "src/ms8/connect/mcp_server/mcp_server.py"
    text = path.read_text(encoding="utf-8")
    helpers = '''

def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _audit_read_allowed(params: dict[str, Any]) -> bool:
    required_token = str(os.environ.get("MS8_CONNECT_CLIENT_TOKEN", "")).strip()
    if not required_token:
        return False
    if not _as_bool(os.environ.get("MS8_CONNECT_AUDIT_READ", "0")):
        return False
    ok, _ = _enforce_client_token(params)
    return ok


def _memory_error(tool: str, exc: Exception) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "invalid_request",
        "error": "invalid_memory_tool_request",
        "error_code": "E_MCP_INVALID_REQUEST",
        "reason": str(exc),
        "tool": tool,
    }


def _call_memory_tool(tool: str, params: dict[str, Any], svc: MemoryServiceInterface) -> dict[str, Any]:
    include_blocked = _as_bool(params.get("include_blocked", False))
    if include_blocked and not _audit_read_allowed(params):
        out = {
            "ok": False,
            "status": "forbidden",
            "error": "audit_read_not_allowed",
            "error_code": "E_MCP_AUDIT_READ_FORBIDDEN",
            "tool": tool,
        }
        _audit(tool, False, {"client": _get_client_name(params), "audit_view": True})
        return out
    try:
        if tool == "memory_catalog":
            out = svc.memory_catalog(include_blocked=True) if include_blocked else svc.memory_catalog()
        elif tool == "memory_list":
            kwargs: dict[str, Any] = {
                "offset": int(params.get("offset", 0) or 0),
                "limit": int(params.get("limit", 100) or 100),
                "view": str(params.get("view") or "summary"),
                "source": str(params.get("source") or ""),
                "category": str(params.get("category") or ""),
                "status": str(params.get("status") or ""),
            }
            if include_blocked:
                kwargs["include_blocked"] = True
            out = svc.memory_list(**kwargs)
        elif tool == "memory_get":
            kwargs = {"view": str(params.get("view") or "full")}
            if include_blocked:
                kwargs["include_blocked"] = True
            out = svc.memory_get(str(params.get("id") or params.get("memory_id") or ""), **kwargs)
        elif tool == "memory_search":
            out = svc.memory_search(
                str(params.get("text") or params.get("query") or ""),
                limit=int(params.get("limit", 20) or 20),
                view=str(params.get("view") or "summary"),
            )
        else:
            return {"ok": False, "error": f"unknown_tool:{tool}"}
    except (OSError, TypeError, ValueError) as exc:
        out = _memory_error(tool, exc)
    _audit(tool, bool(out.get("ok", False)), {"client": _get_client_name(params), "audit_view": include_blocked})
    return out
'''
    text = replace_once(
        text,
        "def _write_allowed() -> bool:\n    deny = str(os.environ.get(\"MS8_CONNECT_READONLY\", \"\")).strip().lower()\n    return deny not in {\"1\", \"true\", \"yes\", \"on\"}\n\n\ndef _audit(",
        "def _write_allowed() -> bool:\n    deny = str(os.environ.get(\"MS8_CONNECT_READONLY\", \"\")).strip().lower()\n    return deny not in {\"1\", \"true\", \"yes\", \"on\"}\n"
        + helpers
        + "\n\ndef _audit(",
        "mcp server helpers",
    )
    text = regex_once(
        text,
        r"\n    if tool == \"memory_catalog\":.*?\n    return \{\"ok\": False, \"error\": f\"unknown_tool:\{name\}\"\}",
        "\n    if tool.startswith(\"memory_\"):\n        return _call_memory_tool(tool, p, svc)\n    return {\"ok\": False, \"error\": f\"unknown_tool:{name}\"}",
        "memory tool dispatch",
    )
    new_resource = '''def read_resource(
    key: str,
    config: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    p = params if isinstance(params, dict) else {}
    ok_token, token_err = _enforce_client_token(p)
    if not ok_token:
        out = {"ok": False, "error": token_err, "resource": key, "client": _get_client_name(p)}
        _audit("resource_auth", False, out)
        return out
    cfg = _load_config(config)
    svc = MemoryServiceInterface.from_config(cfg)
    if key == "catalog":
        return svc.memory_catalog()
    if key.startswith("memory/"):
        return {
            "ok": False,
            "error": "dynamic_memory_resource_disabled",
            "error_code": "E_MCP_RESOURCE_DISABLED",
            "resource": key,
        }
    return svc.profile(key)
'''
    text = regex_once(
        text,
        r"def read_resource\(key: str, config: dict\[str, Any\] \| None = None\) -> dict\[str, Any\]:.*\Z",
        new_resource,
        "resource reader",
    )
    path.write_text(text, encoding="utf-8")


def patch_stdio_server() -> None:
    path = ROOT / "src/ms8/connect/mcp_server/stdio_server.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '"inputSchema": {"type": "object", "properties": {}, "additionalProperties": True},\n    },\n    "memory_list": {',
        '"inputSchema": {\n            "type": "object",\n            "properties": {"include_blocked": {"type": "boolean", "default": False}},\n            "additionalProperties": True,\n        },\n    },\n    "memory_list": {',
        "catalog schema",
    )
    text = replace_once(
        text,
        '                "status": {"type": "string"},\n            },\n            "additionalProperties": True,\n        },\n    },\n    "memory_get": {',
        '                "status": {"type": "string"},\n                "include_blocked": {"type": "boolean", "default": False},\n            },\n            "additionalProperties": True,\n        },\n    },\n    "memory_get": {',
        "list schema",
    )
    text = replace_once(
        text,
        '                "view": {"type": "string", "enum": ["summary", "full"], "default": "full"},\n            },\n            "required": ["id"],',
        '                "view": {"type": "string", "enum": ["summary", "full"], "default": "full"},\n                "include_blocked": {"type": "boolean", "default": False},\n            },\n            "required": ["id"],',
        "get schema",
    )
    text = replace_once(
        text,
        '        out = call_tool(name, arguments)\n        text = json.dumps(out, ensure_ascii=False)\n',
        '        try:\n            out = call_tool(name, arguments)\n        except (OSError, TypeError, ValueError) as exc:\n            out = {\n                "ok": False,\n                "status": "invalid_request",\n                "error": "tool_call_failed",\n                "error_code": "E_MCP_TOOL_CALL_FAILED",\n                "reason": str(exc),\n            }\n        text = json.dumps(out, ensure_ascii=False)\n',
        "stdio tool error boundary",
    )
    text = replace_once(
        text,
        '        out = read_resource(key)\n        text = json.dumps(out, ensure_ascii=False)\n',
        '        try:\n            out = read_resource(key, params=params)\n        except (OSError, TypeError, ValueError) as exc:\n            out = {\n                "ok": False,\n                "status": "invalid_request",\n                "error": "resource_read_failed",\n                "error_code": "E_MCP_RESOURCE_READ_FAILED",\n                "reason": str(exc),\n            }\n        text = json.dumps(out, ensure_ascii=False)\n',
        "stdio resource auth boundary",
    )
    path.write_text(text, encoding="utf-8")


def patch_tests() -> None:
    service_path = ROOT / "tests/test_memory_service_interface_edges.py"
    text = service_path.read_text(encoding="utf-8")
    addition = '''


def test_memory_default_visibility_and_explicit_audit_redaction(tmp_path: Path) -> None:
    records = tmp_path / "memory" / "auto_memory_records.jsonl"
    records.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"id": "ok", "text": "visible", "normalized_text": "visible", "status": "accepted", "source": "ask", "category": "general", "created_at": "2026-01-01T00:00:00Z", "scope": "personal", "authority": "user_explicit", "sensitivity": "private", "can_recall": True},
        {"id": "candidate", "text": "candidate", "normalized_text": "candidate", "status": "candidate", "source": "ask", "category": "general", "created_at": "2026-01-02T00:00:00Z", "scope": "personal", "authority": "user_explicit", "sensitivity": "private", "can_recall": True},
        {"id": "secret", "text": "password=abc", "normalized_text": "password=abc", "status": "accepted", "source": "ask", "category": "general", "created_at": "2026-01-03T00:00:00Z", "scope": "personal", "authority": "user_explicit", "sensitivity": "secret", "can_recall": True, "token": "abc"},
        {"id": "disabled", "text": "disabled", "normalized_text": "disabled", "status": "accepted", "source": "ask", "category": "general", "created_at": "2026-01-04T00:00:00Z", "scope": "personal", "authority": "user_explicit", "sensitivity": "private", "can_recall": False},
    ]
    records.write_text("".join(json.dumps(row) + "\\n" for row in rows), encoding="utf-8")

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
'''
    if "test_memory_default_visibility_and_explicit_audit_redaction" not in text:
        text += addition
    service_path.write_text(text, encoding="utf-8")

    server_path = ROOT / "tests/test_connect_module_and_mcp_server_helpers.py"
    text = server_path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '    memory = server.read_resource("memory/m1", {})\n    assert memory["ok"] is True\n    assert memory["id"] == "m1"\n',
        '    memory = server.read_resource("memory/m1", {})\n    assert memory["ok"] is False\n    assert memory["error"] == "dynamic_memory_resource_disabled"\n',
        "resource test",
    )
    addition = '''


def test_mcp_audit_read_requires_explicit_flag_and_token(monkeypatch) -> None:
    class _Svc:
        @classmethod
        def from_config(cls, _cfg):
            return cls()

        def memory_catalog(self, include_blocked=False):
            return {"ok": True, "include_blocked": include_blocked}

    monkeypatch.setattr(server, "MemoryServiceInterface", _Svc)
    monkeypatch.setattr(server, "_load_config", lambda _cfg=None: {})
    monkeypatch.setattr(server, "_audit", lambda *_a, **_k: None)
    monkeypatch.setenv("MS8_CONNECT_CLIENT_TOKEN", "tok")
    monkeypatch.delenv("MS8_CONNECT_AUDIT_READ", raising=False)

    denied = server.call_tool("memory_catalog", {"token": "tok", "include_blocked": True})
    assert denied["ok"] is False
    assert denied["error_code"] == "E_MCP_AUDIT_READ_FORBIDDEN"

    monkeypatch.setenv("MS8_CONNECT_AUDIT_READ", "1")
    allowed = server.call_tool("memory_catalog", {"token": "tok", "include_blocked": True})
    assert allowed["ok"] is True
    assert allowed["include_blocked"] is True


def test_mcp_memory_invalid_arguments_are_structured(monkeypatch) -> None:
    class _Svc:
        @classmethod
        def from_config(cls, _cfg):
            return cls()

        def memory_list(self, **_kwargs):
            raise ValueError("limit must be between 1 and 500")

    monkeypatch.setattr(server, "MemoryServiceInterface", _Svc)
    monkeypatch.setattr(server, "_load_config", lambda _cfg=None: {})
    monkeypatch.setattr(server, "_audit", lambda *_a, **_k: None)
    monkeypatch.delenv("MS8_CONNECT_CLIENT_TOKEN", raising=False)
    out = server.call_tool("memory_list", {"limit": 501})
    assert out["ok"] is False
    assert out["error_code"] == "E_MCP_INVALID_REQUEST"


def test_resource_read_enforces_configured_client_token(monkeypatch) -> None:
    class _Svc:
        @classmethod
        def from_config(cls, _cfg):
            return cls()

        def memory_catalog(self):
            return {"ok": True}

    monkeypatch.setattr(server, "MemoryServiceInterface", _Svc)
    monkeypatch.setattr(server, "_load_config", lambda _cfg=None: {})
    monkeypatch.setattr(server, "_audit", lambda *_a, **_k: None)
    monkeypatch.setenv("MS8_CONNECT_CLIENT_TOKEN", "tok")
    denied = server.read_resource("catalog", params={})
    assert denied["ok"] is False
    assert denied["error"] == "invalid_client_token"
    allowed = server.read_resource("catalog", params={"token": "tok"})
    assert allowed["ok"] is True
'''
    if "test_mcp_audit_read_requires_explicit_flag_and_token" not in text:
        text += addition
    server_path.write_text(text, encoding="utf-8")

    stdio_path = ROOT / "tests/test_connect_stdio_server.py"
    text = stdio_path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '    monkeypatch.setattr(mcp_mod, "read_resource", lambda key: {"ok": True, "key": key})\n',
        '    monkeypatch.setattr(mcp_mod, "read_resource", lambda key, params=None: {"ok": True, "key": key, "params": params})\n',
        "stdio resource mock",
    )
    stdio_path.write_text(text, encoding="utf-8")


def main() -> None:
    policy_path = ROOT / "src/ms8/connect/mcp_server/memory_access_policy.py"
    policy_path.write_text(POLICY_MODULE, encoding="utf-8")
    patch_memory_service()
    patch_mcp_server()
    patch_stdio_server()
    patch_tests()


if __name__ == "__main__":
    main()
