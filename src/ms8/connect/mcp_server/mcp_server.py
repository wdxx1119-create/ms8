from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from hashlib import sha1
from pathlib import Path
from typing import Any

from ..scripts.common import connect_package_root, connect_root, load_yaml
from .memory_service_interface import MemoryServiceInterface

TOOL_NAMES = (
    "prepare_reply",
    "submit",
    "batch_submit",
    "query",
    "context",
    "status",
    "profile",
    "memory_catalog",
    "memory_list",
    "memory_get",
    "memory_search",
)
RESOURCE_KEYS = ("long-term", "profile", "recent", "catalog")
_SUBMIT_GUARD_FILE = "submit_guard_state.json"
_COOLDOWN_SECONDS = 180
_MIN_LEN = 5
_MAX_RECENT_HASH = 300
logger = logging.getLogger(__name__)


def _expand(raw: str) -> Path:
    return Path(str(raw or "")).expanduser()


def _load_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    if isinstance(config, dict) and config:
        return config
    return load_yaml(connect_package_root() / "config" / "mcp_config.yaml")


def _load_registry() -> dict[str, Any]:
    path = connect_package_root() / "adapter_registry" / "adapters.json"
    if not path.exists():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        logger.debug("Failed to load adapter registry: %s", exc)
        return {}
    return obj if isinstance(obj, dict) else {}


def _source_tag(name: str) -> str:
    return f"mcp:{str(name or 'unknown').strip().lower()}"


def _get_client_name(params: dict[str, Any] | None = None) -> str:
    p = params if isinstance(params, dict) else {}
    return str(p.get("client") or p.get("client_name") or "anonymous").strip()


def _enforce_client_token(params: dict[str, Any] | None = None) -> tuple[bool, str]:
    required = str(os.environ.get("MS8_CONNECT_CLIENT_TOKEN", "")).strip()
    if not required:
        return True, ""
    p = params if isinstance(params, dict) else {}
    got = str(p.get("token") or "").strip()
    if got == required:
        return True, ""
    return False, "invalid_client_token"


def _write_allowed() -> bool:
    deny = str(os.environ.get("MS8_CONNECT_READONLY", "")).strip().lower()
    return deny not in {"1", "true", "yes", "on"}


def _audit(action: str, ok: bool, detail: dict[str, Any] | None = None) -> None:
    root = connect_root()
    p = root / "logs" / "audit.log"
    d = detail if isinstance(detail, dict) else {}
    with p.open("a", encoding="utf-8") as f:
        f.write(f"{action} ok={bool(ok)} detail={json.dumps(d, ensure_ascii=False)}\n")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_text(text: str) -> str:
    base = str(text or "").strip().lower()
    base = re.sub(r"\s+", " ", base)
    return base


def _text_hash(text: str) -> str:
    return sha1(_normalize_text(text).encode("utf-8", errors="ignore")).hexdigest()


def _guard_state_path() -> Path:
    return connect_root() / "runtime" / _SUBMIT_GUARD_FILE


def _load_guard_state() -> dict[str, Any]:
    p = _guard_state_path()
    if not p.exists():
        return {"recent": {}}
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        logger.debug("Failed to load submit guard state: %s", exc)
        return {"recent": {}}
    return obj if isinstance(obj, dict) else {"recent": {}}


def _save_guard_state(state: dict[str, Any]) -> None:
    p = _guard_state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _is_low_value_text(text: str) -> tuple[bool, str]:
    raw = str(text or "").strip()
    if len(raw) < _MIN_LEN:
        return True, "too_short"
    low = raw.lower()
    noise_phrases = (
        "ok",
        "好的",
        "收到",
        "知道了",
        "thanks",
        "thank you",
        "chat history",
        "replied message",
        "untrusted, for context",
    )
    if low in noise_phrases:
        return True, "trivial_phrase"
    if re.fullmatch(r"[\W_]+", raw):
        return True, "punctuation_only"
    if re.fullmatch(r"\d+(\.\d+)?", raw):
        return True, "number_only"
    return False, ""


def _guard_admission(payload: dict[str, Any]) -> tuple[bool, str]:
    text = str(payload.get("content") or "")
    low_value, reason = _is_low_value_text(text)
    if low_value:
        return False, reason
    digest = _text_hash(text)
    state = _load_guard_state()
    recent = state.get("recent", {})
    if not isinstance(recent, dict):
        recent = {}
    now = datetime.now(timezone.utc)
    prev = recent.get(digest)
    if isinstance(prev, str):
        try:
            ts = datetime.fromisoformat(prev.replace("Z", "+00:00"))
            age = (now - ts).total_seconds()
            if age < _COOLDOWN_SECONDS:
                return False, "cooldown_duplicate"
        except (TypeError, ValueError) as exc:
            logger.debug("Failed to parse previous submit timestamp: %s", exc)
    recent[digest] = _now_iso()
    # keep last N
    if len(recent) > _MAX_RECENT_HASH:
        sorted_items = sorted(recent.items(), key=lambda kv: str(kv[1]), reverse=True)[:_MAX_RECENT_HASH]
        recent = dict(sorted_items)
    state["recent"] = recent
    _save_guard_state(state)
    return True, ""


def create_server(config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = _load_config(config)
    svc = MemoryServiceInterface.from_config(cfg)
    return {
        "ok": True,
        "service": svc.status(),
        "tools": list(TOOL_NAMES),
        "resources": list(RESOURCE_KEYS),
        "registry_count": len(_load_registry()),
    }


def list_tools() -> list[str]:
    return list(TOOL_NAMES)


def list_resources() -> list[str]:
    return list(RESOURCE_KEYS)


def call_tool(name: str, params: dict[str, Any] | None = None, config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = _load_config(config)
    svc = MemoryServiceInterface.from_config(cfg)
    p = params if isinstance(params, dict) else {}
    ok_token, token_err = _enforce_client_token(p)
    if not ok_token:
        out = {"ok": False, "error": token_err, "client": _get_client_name(p)}
        _audit("tool_auth", False, out)
        return out
    tool = str(name or "").strip().lower()
    if tool == "prepare_reply":
        text = str(p.get("text") or p.get("message") or p.get("query") or "").strip()
        limit = int(p.get("limit", 5) or 5)
        out = svc.context(text, limit)
        if isinstance(out, dict):
            out["must_call_before_answer"] = True
            out["workflow"] = {
                "step1": "Use context/system_prompt_extra before answering.",
                "step2": "After answering, submit durable facts/preferences/decisions.",
                "step3": "Use batch_submit when multiple durable items exist.",
            }
        _audit("prepare_reply", bool(out.get("ok", False)), {"client": _get_client_name(p)})
        return out
    if tool == "submit":
        if not _write_allowed():
            out = {"ok": False, "error": "readonly_mode", "tool": "submit"}
            _audit("submit", False, out)
            return out
        payload = dict(p)
        payload.setdefault("source", _source_tag("submit"))
        accepted, reason = _guard_admission(payload)
        if not accepted:
            out = {"ok": False, "accepted": False, "error": "guard_rejected", "reason": reason, "tool": "submit"}
            _audit("submit", False, {"client": _get_client_name(p), "reason": reason})
            return out
        out = svc.submit(payload)
        _audit("submit", bool(out.get("ok", False)), {"client": _get_client_name(p)})
        return out
    if tool == "batch_submit":
        if not _write_allowed():
            out = {"ok": False, "error": "readonly_mode", "tool": "batch_submit"}
            _audit("batch_submit", False, out)
            return out
        memories = p.get("memories", [])
        if not isinstance(memories, list) or not memories:
            out = {"ok": False, "error": "invalid_memories", "detail": "memories must be a non-empty array"}
            _audit("batch_submit", False, {"client": _get_client_name(p), "error": out.get("error")})
            return out
        results: list[dict[str, Any]] = []
        accepted_count = 0
        for row in memories:
            payload = dict(row) if isinstance(row, dict) else {"content": str(row or "")}
            payload.setdefault("source", _source_tag("batch_submit"))
            accepted_guard, reason = _guard_admission(payload)
            if not accepted_guard:
                result = {"ok": False, "accepted": False, "error": "guard_rejected", "reason": reason}
            else:
                result = svc.submit(payload)
            results.append(result if isinstance(result, dict) else {"ok": False, "error": "submit_failed"})
            if bool(isinstance(result, dict) and result.get("ok", False)):
                accepted_count += 1
        out = {
            "ok": accepted_count > 0,
            "total": len(memories),
            "accepted": accepted_count,
            "rejected": int(len(memories) - accepted_count),
            "results": results,
        }
        _audit(
            "batch_submit",
            bool(out.get("ok", False)),
            {"client": _get_client_name(p), "total": out["total"], "accepted": out["accepted"]},
        )
        return out
    if tool == "query":
        out = svc.query(str(p.get("text") or p.get("query") or ""), int(p.get("top_k", 5) or 5))
        _audit("query", bool(out.get("ok", False)), {"client": _get_client_name(p)})
        return out
    if tool == "context":
        out = svc.context(str(p.get("text") or p.get("message") or ""), int(p.get("limit", 5) or 5))
        _audit("context", bool(out.get("ok", False)), {"client": _get_client_name(p)})
        return out
    if tool == "status":
        out = svc.quick_status()
        _audit("status", bool(out.get("ok", False)), {"client": _get_client_name(p)})
        return out
    if tool == "profile":
        out = svc.profile(str(p.get("key") or p.get("resource") or "profile"))
        _audit("profile", bool(out.get("ok", False)), {"client": _get_client_name(p)})
        return out
    if tool == "memory_catalog":
        out = svc.memory_catalog()
        _audit("memory_catalog", bool(out.get("ok", False)), {"client": _get_client_name(p)})
        return out
    if tool == "memory_list":
        out = svc.memory_list(
            offset=int(p.get("offset", 0) or 0),
            limit=int(p.get("limit", 100) or 100),
            view=str(p.get("view") or "summary"),
            source=str(p.get("source") or ""),
            category=str(p.get("category") or ""),
            status=str(p.get("status") or ""),
        )
        _audit("memory_list", bool(out.get("ok", False)), {"client": _get_client_name(p)})
        return out
    if tool == "memory_get":
        out = svc.memory_get(
            str(p.get("id") or p.get("memory_id") or ""),
            view=str(p.get("view") or "full"),
        )
        _audit("memory_get", bool(out.get("ok", False)), {"client": _get_client_name(p)})
        return out
    if tool == "memory_search":
        out = svc.memory_search(
            str(p.get("text") or p.get("query") or ""),
            limit=int(p.get("limit", 20) or 20),
            view=str(p.get("view") or "summary"),
        )
        _audit("memory_search", bool(out.get("ok", False)), {"client": _get_client_name(p)})
        return out
    return {"ok": False, "error": f"unknown_tool:{name}"}


def read_resource(key: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = _load_config(config)
    svc = MemoryServiceInterface.from_config(cfg)
    if key == "catalog":
        return svc.memory_catalog()
    if key.startswith("memory/"):
        return svc.memory_get(key.split("/", 1)[1], view="full")
    return svc.profile(key)
