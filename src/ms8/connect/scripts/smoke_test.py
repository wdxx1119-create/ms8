from __future__ import annotations

import logging
from typing import Any

from ..mcp_server.memory_service_interface import MemoryServiceInterface
from .client_config import selected_targets
from .common import append_audit, connect_package_root, load_yaml

logger = logging.getLogger(__name__)


def _json_safe(payload: Any) -> Any:
    try:
        import json

        json.dumps(payload, ensure_ascii=False)
        return payload
    except (TypeError, ValueError) as exc:
        logger.debug("Failed to JSON-serialize smoke payload: %s", exc)
        return str(payload)


def _compact_value(payload: Any) -> Any:
    if isinstance(payload, dict):
        return {k: _compact_value(v) for k, v in payload.items() if k not in {"health", "context"}}
    if isinstance(payload, list):
        return [_compact_value(x) for x in payload[:5]]
    return payload


def run_smoke_test(config: dict[str, Any] | None = None, *, target: str = "all") -> dict[str, Any]:
    cfg = config if isinstance(config, dict) else load_yaml(connect_package_root() / "config" / "mcp_config.yaml")
    svc = MemoryServiceInterface.from_config(cfg)
    steps: list[dict[str, Any]] = []

    submit_out = svc.submit({"content": "connect smoke save", "source": "mcp:smoke_test", "category": "system"})
    steps.append({"name": "save", "ok": bool(submit_out.get("ok", False)), "detail": submit_out})
    append_audit(f'save_memory source=mcp:smoke_test ok={submit_out.get("ok", False)}')

    query_out = svc.query("connect smoke", top_k=5)
    steps.append({"name": "search", "ok": bool(query_out.get("ok", False)), "detail": query_out})

    ctx_out = svc.context("connect smoke", limit=5)
    steps.append({"name": "context", "ok": bool(ctx_out.get("ok", False)), "detail": ctx_out})

    status_out = svc.status()
    steps.append({"name": "status", "ok": bool(status_out.get("ok", False)), "detail": status_out})

    overall_ok = all(bool(s.get("ok", False)) for s in steps)
    return {
        "ok": overall_ok,
        "overall_ok": overall_ok,
        "target": target,
        "target_profiles": selected_targets(target),
        "negotiation": {
            "mode": "profile_targeted",
            "fallback": "use `ms8 connect apply --target <name>` if client config missing",
        },
        "steps": steps,
        "failed_steps": [s["name"] for s in steps if not s.get("ok", False)],
        "compact": _compact_value(_json_safe(steps)),
    }


def main(target: str = "all") -> dict[str, Any]:
    return run_smoke_test(target=target)


if __name__ == "__main__":
    out = main()
    print(out)
