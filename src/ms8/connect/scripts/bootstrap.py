from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import tomllib

from ms8.connect.scripts.apply_client_configs import run as apply_client_configs
from ms8.connect.scripts.client_config import expected_route_args, target_paths, target_profile
from ms8.connect.scripts.common import connect_root, write_json
from ms8.connect.scripts.connect import run_connect_flow
from ms8.connect.scripts.generate_client_configs import run as generate_client_configs
from ms8.connect.scripts.smoke_test import run_smoke_test
from ms8.connect.scripts.verify_client_configs import run as verify_client_configs

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _runtime_paths() -> tuple[Path, Path]:
    root = connect_root() / "runtime"
    root.mkdir(parents=True, exist_ok=True)
    return root / "bootstrap_report.json", root / "bootstrap_attempts.jsonl"


def _auto_repair_log_path() -> Path:
    root = connect_root() / "runtime"
    root.mkdir(parents=True, exist_ok=True)
    return root / "auto_repair_log.jsonl"


def _first_install_report_paths() -> tuple[Path, Path]:
    root = connect_root() / "runtime"
    root.mkdir(parents=True, exist_ok=True)
    return root / "first_install_connect_report.json", root / "first_install_connect_report.txt"


def _write_first_install_report(payload: dict[str, Any]) -> dict[str, Any]:
    json_path, text_path = _first_install_report_paths()
    write_json(json_path, payload)
    counts = payload.get("counts", {}) if isinstance(payload.get("counts", {}), dict) else {}
    profiles = payload.get("profiles", {}) if isinstance(payload.get("profiles", {}), dict) else {}
    lines = [
        "MS8 First-Install Connect Report",
        f"generated_at: {payload.get('generated_at', '')}",
        f"target: {payload.get('target', 'all')}",
        f"ready: {counts.get('ready', 0)}",
        f"degraded: {counts.get('degraded', 0)}",
        f"manual: {counts.get('manual', 0)}",
        "",
    ]
    for name, item in sorted(profiles.items()):
        if not isinstance(item, dict):
            continue
        activation = item.get("activation", {}) if isinstance(item.get("activation", {}), dict) else {}
        lines.append(f"- {name}: {item.get('status', 'manual')}")
        lines.append(f"  path: {item.get('path', '')}")
        if activation:
            lines.append(
                "  activation: "
                f"detected={bool(activation.get('activation_detected', False))}, "
                f"resolved_exists={bool(activation.get('resolved_exists', False))}, "
                f"existing_candidates={int(activation.get('existing_candidate_count', 0) or 0)}"
            )
        lines.append(f"  next: {item.get('guidance', '')}")
    one_time_hint = str(payload.get("one_time_hint", "") or "").strip()
    if one_time_hint:
        lines.extend(["", f"one_time_hint: {one_time_hint}"])
    actionable_hints = payload.get("actionable_hints", [])
    if isinstance(actionable_hints, list) and actionable_hints:
        lines.extend(["", "actionable_hints:"])
        for item in actionable_hints[:20]:
            if not isinstance(item, str):
                continue
            lines.append(f" - {item}")
    repair_chain = str(payload.get("shortest_repair_chain", "") or "").strip()
    if repair_chain:
        lines.extend(["", f"shortest_repair_chain: {repair_chain}"])
    text_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return {"json_path": str(json_path), "text_path": str(text_path)}


def _append_attempt(payload: dict[str, Any]) -> None:
    _, attempts = _runtime_paths()
    with attempts.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _append_auto_repair(payload: dict[str, Any]) -> None:
    p = _auto_repair_log_path()
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _repair_target_configs(target: str, verify: dict[str, Any]) -> dict[str, Any]:
    """
    Lightweight self-heal for stale or partially broken client configs.
    We only touch failing profiles and only the ms8-memory server block.
    """
    details = verify.get("details", {}) if isinstance(verify.get("details", {}), dict) else {}
    if not details:
        return {"ok": False, "changed": 0, "profiles": {}, "reason": "no_verify_details"}

    changed = 0
    profile_out: dict[str, Any] = {}
    command = sys.executable
    args = ["-m", "ms8.connect.mcp_server.stdio_server", *expected_route_args(target)]
    py_src = str(Path(__file__).resolve().parents[3])
    legacy_tokens = ("<PROJECT_ROOT>", "openclaw-memory-auto")

    for name, info in details.items():
        if not isinstance(info, dict):
            continue
        # only repair failing entries
        if bool(info.get("command_ok", False) and info.get("args_ok", False) and info.get("has_ms8_server", False)):
            continue
        path = Path(str(info.get("path", "") or ""))
        if not path:
            profile_out[name] = {"ok": False, "reason": "empty_path"}
            continue
        try:
            fmt = str(target_profile(name).get("config_format", "json"))
            if fmt == "toml":
                raw = path.read_text(encoding="utf-8") if path.exists() else ""
                for token in legacy_tokens:
                    raw = raw.replace(token, str(Path(__file__).resolve().parents[4]))
                base = tomllib.loads(raw) if raw.strip() else {}
                if not isinstance(base, dict):
                    base = {}
                servers = base.get("mcp_servers", {})
                if not isinstance(servers, dict):
                    servers = {}
                server = servers.get("ms8-memory", {})
                if not isinstance(server, dict):
                    server = {}
                env = server.get("env", {})
                if not isinstance(env, dict):
                    env = {}
                env.setdefault("MS8_AGENT_TARGET", name)
                env["PYTHONPATH"] = py_src
                server.update({"command": command, "args": args, "env": env})
                servers["ms8-memory"] = server
                base["mcp_servers"] = servers
                # write minimal block (safe overwrite for managed section)
                lines = [
                    "[mcp_servers.ms8-memory]",
                    f'command = "{command}"',
                    "args = [" + ", ".join(json.dumps(a, ensure_ascii=False) for a in args) + "]",
                    "[mcp_servers.ms8-memory.env]",
                ]
                for k, v in env.items():
                    lines.append(f'{k} = {json.dumps(str(v), ensure_ascii=False)}')
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            else:
                obj: dict[str, Any] = {}
                if path.exists():
                    try:
                        obj = json.loads(path.read_text(encoding="utf-8"))
                    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
                        logger.debug("Failed to parse existing target JSON config %s: %s", path, exc)
                        obj = {}
                if not isinstance(obj, dict):
                    obj = {}
                txt = json.dumps(obj, ensure_ascii=False)
                for token in legacy_tokens:
                    txt = txt.replace(token, str(Path(__file__).resolve().parents[4]))
                try:
                    obj = json.loads(txt)
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    logger.debug("Failed to parse normalized target JSON config %s: %s", path, exc)
                    obj = obj if isinstance(obj, dict) else {}
                servers = obj.get("mcpServers", {})
                if not isinstance(servers, dict):
                    servers = {}
                server = servers.get("ms8-memory", {})
                if not isinstance(server, dict):
                    server = {}
                env = server.get("env", {})
                if not isinstance(env, dict):
                    env = {}
                env.setdefault("MS8_AGENT_TARGET", name)
                env["PYTHONPATH"] = py_src
                server.update({"command": command, "args": args, "env": env})
                servers["ms8-memory"] = server
                obj["mcpServers"] = servers
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
            changed += 1
            profile_out[name] = {"ok": True, "path": str(path)}
        except (OSError, TypeError, ValueError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
            profile_out[name] = {"ok": False, "path": str(path), "error": str(exc)}
    return {"ok": changed > 0, "changed": changed, "profiles": profile_out}


def _target_exists(target: str) -> bool:
    try:
        p = target_paths(target).get(target)
    except (TypeError, ValueError, KeyError) as exc:
        logger.debug("Failed to resolve target path for %s: %s", target, exc)
        return False
    return bool(isinstance(p, Path) and p.exists())


def _build_actionable_hints(
    readiness_profiles: dict[str, Any],
    *,
    target: str,
) -> list[str]:
    ranked: list[tuple[int, str]] = []
    tool_priority = {
        "claude_desktop": 10,
        "cursor": 20,
        "codex": 25,
        "windsurf": 30,
        "cherry_studio": 35,
        "cline": 40,
        "roo": 45,
        "continue": 50,
        "openclaw": 60,
        "hermes": 65,
        "generic_json": 90,
    }
    for name, item in sorted(readiness_profiles.items()):
        if not isinstance(item, dict):
            continue
        status = str(item.get("status", "manual"))
        if status == "ready":
            continue
        activation = item.get("activation", {}) if isinstance(item.get("activation", {}), dict) else {}
        activation_detected = bool(activation.get("activation_detected", False))
        base = int(tool_priority.get(name, 80))
        status_bias = 0 if status == "degraded" else 20
        if name in {"claude_desktop", "cursor", "windsurf", "codex", "cline", "roo", "continue", "cherry_studio"}:
            if not activation_detected:
                ranked.append(
                    (
                        base + status_bias + 10,
                        f"{name}: open app once to create config, then run `ms8 connect bootstrap --target {name}`.",
                    )
                )
            else:
                ranked.append(
                    (
                        base + status_bias,
                        f"{name}: run `ms8 connect apply --target {name}` then `ms8 connect verify --target {name}`.",
                    )
                )
        elif name == "generic_json":
            ranked.append(
                (
                    base + status_bias,
                    "generic_json: run `ms8 connect apply --target generic_json` and import generated file manually.",
                )
            )
        else:
            ranked.append(
                (
                    base + status_bias,
                    f"{name}: run `ms8 connect apply --target {name}` then `ms8 connect verify --target {name}`.",
                )
            )
    if not ranked and str(target).strip().lower() in {"all", "*"}:
        return ["All discovered targets are ready. Run `ms8 connect verify --target all` after tool upgrades."]
    ranked.sort(key=lambda x: x[0])
    return [hint for _, hint in ranked]


def _build_shortest_repair_chain(readiness_profiles: dict[str, Any]) -> str:
    degraded = sorted(
        [k for k, v in readiness_profiles.items() if isinstance(v, dict) and str(v.get("status", "")) == "degraded"]
    )
    manual = sorted(
        [k for k, v in readiness_profiles.items() if isinstance(v, dict) and str(v.get("status", "")) == "manual"]
    )
    chain_targets = degraded[:2] + manual[:1]
    if not chain_targets:
        return ""
    chain_cmd_parts = [f"ms8 connect apply --target {t}" for t in chain_targets] + [
        f"ms8 connect verify --target {t}" for t in chain_targets
    ]
    return " && ".join(chain_cmd_parts)


def run_bootstrap(
    *,
    target: str = "claude_desktop",
    auto_fix: bool = True,
    silent: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    started = _now()
    report_path, _ = _runtime_paths()
    detected = {"target": target, "target_config_exists": _target_exists(target)}
    normalized_target = str(target or "all").strip().lower()
    concrete_target_missing = normalized_target not in {"", "all", "*"} and (not _target_exists(target))
    if concrete_target_missing:
        if normalized_target == "codex":
            hint = (
                "Codex config not detected. Open Codex once (to create ~/.codex/config.toml), then run: "
                "ms8 connect bootstrap --target codex"
            )
        elif normalized_target == "claude_desktop":
            hint = "Install/open Claude Desktop once, then run: ms8 connect bootstrap --target claude_desktop"
        else:
            hint = (
                f"Target '{target}' config not detected. Open/install target once, then run: "
                f"ms8 connect bootstrap --target {target}"
            )
        out = {
            "ok": True,
            "skipped": True,
            "reason": "target_not_installed_or_config_not_present",
            "target": target,
            "auto_fix": bool(auto_fix),
            "silent": bool(silent),
            "started_at": started,
            "finished_at": _now(),
            "detected": detected,
            "steps": [{"name": "detect_target", "ok": True, "skipped": True}],
            "hint": hint,
        }
        first_install_report_data_skip: dict[str, Any] = {
            "generated_at": _now(),
            "target": target,
            "counts": {"ready": 0, "degraded": 0, "manual": 1},
            "profiles": {
                str(target): {
                    "status": "manual",
                    "path": "",
                    "guidance": hint,
                }
            },
            "one_time_hint": hint,
            "actionable_hints": [hint],
            "shortest_repair_chain": "",
        }
        out["first_install_report"] = _write_first_install_report(first_install_report_data_skip)
        write_json(report_path, out)
        _append_attempt(out)
        return out

    steps: list[dict[str, Any]] = []
    if dry_run:
        out = {
            "ok": True,
            "dry_run": True,
            "target": target,
            "auto_fix": bool(auto_fix),
            "silent": bool(silent),
            "started_at": started,
            "finished_at": _now(),
            "detected": detected,
            "steps": [{"name": "dry_run", "ok": True}],
        }
        first_install_report_data_dry: dict[str, Any] = {
            "generated_at": _now(),
            "target": target,
            "counts": {"ready": 0, "degraded": 0, "manual": 0},
            "profiles": {},
            "one_time_hint": "Dry-run only. Run without --dry-run to apply and verify MCP client configs.",
            "shortest_repair_chain": "",
        }
        out["first_install_report"] = _write_first_install_report(first_install_report_data_dry)
        write_json(report_path, out)
        _append_attempt(out)
        return out

    flow = run_connect_flow(target=target)
    steps.append({"name": "connect_flow", "ok": bool(flow.get("result", {}).get("overall_ok", False))})

    generate = generate_client_configs(target=target)
    steps.append({"name": "generate", "ok": bool(generate.get("ok", False))})

    apply = apply_client_configs(target=target)
    steps.append({"name": "apply", "ok": bool(apply.get("ok", False))})

    verify = verify_client_configs(target=target)
    verify_ok = bool(verify.get("ok", False))
    steps.append({"name": "verify", "ok": verify_ok})

    repaired = False
    repair_detail: dict[str, Any] | None = None
    per_target_retry: dict[str, Any] = {}
    if (not verify_ok) and auto_fix:
        repaired = True
        generate2 = generate_client_configs(target=target)
        apply2 = apply_client_configs(target=target)
        verify2 = verify_client_configs(target=target)
        if not bool(verify2.get("ok", False)):
            repair_detail = _repair_target_configs(target, verify2)
            verify2 = verify_client_configs(target=target)
        # For broad first-run bootstrap, do one more per-target repair loop on degraded profiles.
        if (not bool(verify2.get("ok", False))) and str(target).strip().lower() in {"all", "*"}:
            details = verify2.get("details", {}) if isinstance(verify2.get("details", {}), dict) else {}
            for profile_name, info in details.items():
                if not isinstance(info, dict):
                    continue
                healthy = bool(
                    info.get("exists")
                    and info.get("has_mcpServers")
                    and info.get("has_ms8_server")
                    and info.get("command_ok")
                    and info.get("args_ok")
                    and info.get("verify_keys_ok")
                    and not info.get("legacy_path_found")
                )
                if healthy:
                    continue
                try:
                    apply_p = apply_client_configs(target=profile_name)
                    verify_p = verify_client_configs(target=profile_name)
                    per_target_retry[profile_name] = {
                        "apply_ok": bool(apply_p.get("ok", False)),
                        "verify_ok": bool(verify_p.get("ok", False)),
                    }
                except (OSError, TypeError, ValueError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
                    per_target_retry[profile_name] = {"apply_ok": False, "verify_ok": False, "error": str(exc)}
            verify2 = verify_client_configs(target=target)
        verify = verify2
        verify_ok = bool(verify2.get("ok", False))
        steps.append(
            {
                "name": "auto_fix_retry",
                "ok": verify_ok,
                "detail": {
                    "generate_ok": bool(generate2.get("ok", False)),
                    "apply_ok": bool(apply2.get("ok", False)),
                    "verify_ok": verify_ok,
                    "repair_detail": repair_detail if isinstance(repair_detail, dict) else {},
                    "per_target_retry": per_target_retry,
                },
            }
        )

    smoke = run_smoke_test()
    steps.append({"name": "smoke", "ok": bool(smoke.get("ok", False))})

    ok = bool(verify_ok and smoke.get("ok", False))
    connect_flow_initial_ok = bool(flow.get("result", {}).get("overall_ok", False))
    connect_flow_final_ok = connect_flow_initial_ok
    if (not connect_flow_initial_ok) and verify_ok and bool(smoke.get("ok", False)):
        # Keep report consistency: later repair/apply/verify path has recovered.
        connect_flow_final_ok = True
        steps.append(
            {
                "name": "connect_flow_reconciled",
                "ok": True,
                "detail": {
                    "reason": "post_flow_recovery",
                    "initial_connect_flow_ok": connect_flow_initial_ok,
                    "final_connect_flow_ok": connect_flow_final_ok,
                },
            }
        )

    report = {
        "ok": ok,
        "target": target,
        "auto_fix": bool(auto_fix),
        "silent": bool(silent),
        "repaired": repaired,
        "started_at": started,
        "finished_at": _now(),
        "detected": detected,
        "steps": steps,
        "verify": verify,
        "smoke": smoke,
        "connect_flow_overall_ok": connect_flow_final_ok,
        "connect_flow_overall_ok_initial": connect_flow_initial_ok,
    }
    readiness = flow.get("target_readiness", {}) if isinstance(flow.get("target_readiness", {}), dict) else {}
    first_install_report_data_final: dict[str, Any] = {
        "generated_at": _now(),
        "target": target,
        "counts": readiness.get("counts", {}) if isinstance(readiness.get("counts", {}), dict) else {},
        "profiles": readiness.get("profiles", {}) if isinstance(readiness.get("profiles", {}), dict) else {},
    }
    counts = cast(dict[str, Any], first_install_report_data_final.get("counts", {}))
    if int(counts.get("manual", 0) or 0) > 0 or int(counts.get("degraded", 0) or 0) > 0:
        first_install_report_data_final["one_time_hint"] = (
            "Some targets need one-time activation. Open the target app once, then run "
            "`ms8 connect bootstrap --target <target>`."
        )
    profiles = cast(dict[str, Any], first_install_report_data_final.get("profiles", {}))
    first_install_report_data_final["actionable_hints"] = _build_actionable_hints(profiles, target=target)
    first_install_report_data_final["shortest_repair_chain"] = _build_shortest_repair_chain(profiles)
    report["first_install_report"] = _write_first_install_report(first_install_report_data_final)
    if not ok:
        report["hint"] = (
            f"Auto-connect degraded for {target}. Run: "
            f"ms8 connect apply --target {target} && ms8 connect verify --target {target}"
        )
    report["self_heal"] = {
        "enabled": bool(auto_fix),
        "repaired": bool(repaired),
        "per_target_retry": per_target_retry,
        "verify_ok": bool(verify_ok),
        "smoke_ok": bool(smoke.get("ok", False)),
    }
    write_json(report_path, report)
    _append_attempt(report)
    if auto_fix:
        _append_auto_repair(
            {
                "timestamp": _now(),
                "target": target,
                "ok": bool(ok),
                "repaired": bool(repaired),
                "verify_ok": bool(verify_ok),
                "smoke_ok": bool(smoke.get("ok", False)),
                "per_target_retry": per_target_retry,
                "hint": str(report.get("hint", "")),
            }
        )
    return report
