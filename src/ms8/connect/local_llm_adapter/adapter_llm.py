from __future__ import annotations

from typing import Any

from ..mcp_server.memory_service_interface import MemoryServiceInterface
from ..scripts.common import connect_package_root, load_yaml


def _load_cfg(config: dict[str, Any] | None = None) -> dict[str, Any]:
    if isinstance(config, dict) and config:
        return config
    return load_yaml(connect_package_root() / "config" / "mcp_config.yaml")


def parse_event_rule_layer(event: dict[str, Any] | None = None) -> dict[str, Any]:
    ev = event if isinstance(event, dict) else {}
    return {
        "content": str(ev.get("content") or ev.get("text") or "").strip(),
        "source": str(ev.get("source") or "adapter:unknown").strip(),
        "category": str(ev.get("category") or "general").strip(),
        "metadata": ev.get("metadata", {}) if isinstance(ev.get("metadata", {}), dict) else {},
    }


def llm_enhance(event: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = parse_event_rule_layer(event)
    return {"ok": True, "enhanced": payload, "model": "local-pass-through"}


def submit_memory_candidate(
    event: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = _load_cfg(config)
    svc = MemoryServiceInterface.from_config(cfg)
    payload = parse_event_rule_layer(event)
    return svc.submit(payload)


def process_event(event: dict[str, Any] | None = None, config: dict[str, Any] | None = None) -> dict[str, Any]:
    enhanced = llm_enhance(event)
    if not enhanced.get("ok", False):
        return enhanced
    return submit_memory_candidate(enhanced.get("enhanced"), config=config)


def get_adapter(config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = _load_cfg(config)
    svc = MemoryServiceInterface.from_config(cfg)
    return {
        "ok": True,
        "status": svc.status(),
        "capabilities": ["submit", "query", "context", "status", "profile"],
    }
