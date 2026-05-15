from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ms8.connect.adapter_registry.registry import load_registry
from ms8.connect.mcp_server.mcp_server import list_resources, list_tools
from ms8.connect.scripts.apply_client_configs import run as apply_client_configs
from ms8.connect.scripts.client_config import payload_for_target, snippet_paths, target_discovery
from ms8.connect.scripts.common import connect_package_root, connect_root, load_yaml, utc_now, write_json
from ms8.connect.scripts.generate_client_configs import run as generate_client_configs
from ms8.connect.scripts.smoke_test import run_smoke_test
from ms8.connect.scripts.verify_client_configs import run as verify_client_configs


def _now() -> str:
    return utc_now()


def _pick_core_python() -> str:
    return "python3"


def _detect_targets() -> dict[str, Any]:
    root = connect_root()
    return {
        "runtime": str(root / "runtime"),
        "logs": str(root / "logs"),
        "client_snippets": str(root / "runtime" / "client_snippets"),
    }


def _step(
    name: str,
    ok: bool,
    detail: dict[str, Any] | None = None,
    *,
    blocking: bool = True,
) -> dict[str, Any]:
    return {
        "name": name,
        "ok": bool(ok),
        "blocking": bool(blocking),
        "detail": detail if isinstance(detail, dict) else {},
    }


def _target_readiness(verify_result: dict[str, Any], target: str) -> dict[str, Any]:
    details = verify_result.get("details", {}) if isinstance(verify_result, dict) else {}
    selected = sorted(snippet_paths(target).keys())
    discovery = target_discovery(target)
    out: dict[str, Any] = {}
    for name in selected:
        info = details.get(name, {}) if isinstance(details.get(name, {}), dict) else {}
        discovered = discovery.get(name, {}) if isinstance(discovery.get(name, {}), dict) else {}
        candidates_raw = discovered.get("candidates", [])
        candidates = [str(x) for x in candidates_raw] if isinstance(candidates_raw, list) else []
        existing_candidates = [p for p in candidates if p and Path(p).exists()]
        resolved_path = str(discovered.get("resolved", "") or "")
        resolved_exists = bool(discovered.get("resolved_exists", False))
        exists = bool(info.get("exists", False))
        has_server = bool(info.get("has_ms8_server", False))
        command_ok = bool(info.get("command_ok", False))
        args_ok = bool(info.get("args_ok", False))
        legacy = bool(info.get("legacy_path_found", False))
        if name == "generic_json":
            if exists and has_server and command_ok and args_ok and not legacy:
                status = "ready"
                guidance = "Generic export file is ready to import into any MCP-compatible client."
            else:
                status = "manual"
                guidance = "Run `ms8 connect apply --target generic_json` to generate export file, then import it in your client."
        elif exists and has_server and command_ok and args_ok and not legacy:
            status = "ready"
            guidance = "No action required."
        elif exists and has_server and not legacy:
            status = "degraded"
            guidance = f"Run `ms8 connect apply --target {name}` then `ms8 connect verify --target {name}`."
        else:
            status = "manual"
            guidance = (
                f"Client config missing/incomplete. Run `ms8 connect generate --target {name}` and "
                f"`ms8 connect apply --target {name}`."
            )
        out[name] = {
            "status": status,
            "path": info.get("path", ""),
            "guidance": guidance,
            "kind": "export" if name == "generic_json" else "client",
            "activation": {
                "strategy": str(discovered.get("strategy", "fixed_path") or "fixed_path"),
                "resolved_path": resolved_path,
                "resolved_exists": resolved_exists,
                "candidate_count": len(candidates),
                "existing_candidate_count": len(existing_candidates),
                "existing_candidates": existing_candidates,
                "activation_detected": resolved_exists or bool(existing_candidates),
            },
            "checks": {
                "exists": exists,
                "has_ms8_server": has_server,
                "command_ok": command_ok,
                "args_ok": args_ok,
                "legacy_path_found": legacy,
            },
        }
    counts = {"ready": 0, "degraded": 0, "manual": 0}
    for item in out.values():
        counts[str(item.get("status", "manual"))] = counts.get(str(item.get("status", "manual")), 0) + 1
    return {"target": target, "counts": counts, "profiles": out}


def run_connect_flow(config: dict[str, Any] | None = None, *, target: str = "all") -> dict[str, Any]:
    pkg_root = connect_package_root()
    runtime_root = connect_root()
    cfg = config if isinstance(config, dict) else load_yaml(pkg_root / "config" / "mcp_config.yaml")
    steps: list[dict[str, Any]] = []

    # 1 detect
    detected = {"config_exists": (pkg_root / "config" / "mcp_config.yaml").exists()}
    steps.append(_step("detect", detected["config_exists"], detected))

    # 2 install
    runtime_dirs_ok = True
    for p in (runtime_root / "runtime", runtime_root / "logs", runtime_root / "runtime" / "client_snippets"):
        p.mkdir(parents=True, exist_ok=True)
        runtime_dirs_ok = runtime_dirs_ok and p.exists()
    steps.append(_step("install", runtime_dirs_ok, {"root": str(runtime_root)}))

    # 3 configure
    cfg_ok = bool(cfg.get("mcp", {}).get("enabled", True))
    steps.append(_step("configure", cfg_ok, {"mcp_enabled": cfg_ok}))

    # 4 smoke_test
    smoke = run_smoke_test(cfg)
    steps.append(_step("smoke_test", bool(smoke.get("ok", False)), smoke))

    # 5 verify
    tools = list_tools()
    resources = list_resources()
    registry = load_registry(pkg_root / "adapter_registry")
    verify_ok = len(tools) >= 5 and len(resources) >= 3 and isinstance(registry, dict) and len(registry) >= 1
    steps.append(
        _step(
            "verify",
            verify_ok,
            {"tools": tools, "resources": resources, "registry_count": len(registry)},
        )
    )

    # 6 report (keep core 6-step contract stable for existing callers/tests)
    overall_ok = all(bool(s.get("ok", False)) for s in steps)
    report: dict[str, Any] = {
        "generated_at": _now(),
        "result": {"overall_ok": overall_ok},
        "steps": steps,
    }
    write_json(runtime_root / "runtime" / "connect_report.json", report)
    write_json(runtime_root / "runtime" / "health.json", {"generated_at": _now(), "mcp_server": {"ok": overall_ok}})
    steps.append(_step("report", True, {"path": str(runtime_root / "runtime" / "connect_report.json")}))
    report["steps"] = steps
    report["result"]["overall_ok"] = all(
        bool(s.get("ok", False))
        for s in steps
        if s["name"] != "report" and bool(s.get("blocking", True))
    )

    # Non-blocking extension steps: automation convenience for local MCP clients.
    generated = generate_client_configs(target=target)
    steps.append(_step("generate_client_configs", bool(generated.get("ok", False)), generated, blocking=False))
    applied = apply_client_configs(target=target)
    steps.append(_step("apply_client_configs", bool(applied.get("ok", False)), applied, blocking=False))
    verified_clients = verify_client_configs(target=target)
    steps.append(_step("verify_client_configs", bool(verified_clients.get("ok", False)), verified_clients, blocking=False))
    report["target_readiness"] = _target_readiness(verified_clients, target)

    # mirror snippets for C6 self-check compatibility
    snippets = runtime_root / "runtime" / "client_snippets"
    snippets.mkdir(parents=True, exist_ok=True)
    for name, rel in snippet_paths(target).items():
        snippet_payload = payload_for_target(name)
        (snippets / rel).write_text(
            json.dumps(snippet_payload, indent=2),
            encoding="utf-8",
        )
    write_json(runtime_root / "runtime" / "connect_report.json", report)
    return report


def _run(config: dict[str, Any] | None = None) -> dict[str, Any]:
    return run_connect_flow(config)


if __name__ == "__main__":
    print(_run())
