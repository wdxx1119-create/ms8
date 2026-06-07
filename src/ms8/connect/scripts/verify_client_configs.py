from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import tomllib

from .client_config import (
    LEGACY_PATH_HINT,
    SERVER_NAME,
    expected_command_signature,
    expected_route_args,
    target_paths,
    target_profile,
)

logger = logging.getLogger(__name__)


def _as_str_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item) for item in value)
    return ()


def _command_matches(command: str, expected_command: str) -> bool:
    """
    Accept equivalent python launchers across environments to avoid false degraded state.
    Example: /usr/bin/python3, /opt/homebrew/bin/python3.14, venv python.
    """
    if command == expected_command:
        return True
    if not command:
        return False
    base = os.path.basename(command)
    exp_base = os.path.basename(expected_command)
    if base.startswith("python") and exp_base.startswith("python"):
        return True
    return False


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        logger.debug("Failed to read JSON %s: %s", path, exc)
        return {}
    return obj if isinstance(obj, dict) else {}


def _read_toml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        obj = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, tomllib.TOMLDecodeError) as exc:
        logger.debug("Failed to read TOML %s: %s", path, exc)
        return {}
    return obj if isinstance(obj, dict) else {}


def run(target: str = "all") -> dict:
    targets = target_paths(target)
    details = {}
    for key, path in targets.items():
        expected_command, expected_prefix_args = expected_command_signature(key)
        expected_args = expected_prefix_args + expected_route_args(key)
        profile = target_profile(key)
        config_format = str(profile.get("config_format", "json"))
        payload = (_read_toml(path) if config_format == "toml" else _read_json(path)) if path.exists() else {}
        if config_format == "toml":
            servers = payload.get("mcp_servers", {}) if isinstance(payload.get("mcp_servers", {}), dict) else {}
            server = servers.get(SERVER_NAME, {}) if isinstance(servers.get(SERVER_NAME, {}), dict) else {}
        else:
            servers = payload.get("mcpServers", {}) if isinstance(payload.get("mcpServers", {}), dict) else {}
            server = servers.get(SERVER_NAME, {}) if isinstance(servers.get(SERVER_NAME, {}), dict) else {}
        command = str(server.get("command") or "")
        args = tuple(server.get("args") or []) if isinstance(server.get("args", []), list) else tuple()
        env = server.get("env", {}) if isinstance(server.get("env", {}), dict) else {}
        verify_keys = _as_str_tuple(profile.get("verify_keys", ()))
        verify_results: dict[str, bool] = {}
        for k in verify_keys:
            if k == "command":
                verify_results[k] = bool(command)
            elif k == "args":
                verify_results[k] = len(args) > 0
            elif k.startswith("env."):
                env_key = k.split(".", 1)[1]
                verify_results[k] = bool(env.get(env_key))
            else:
                verify_results[k] = True
        raw_text = json.dumps(payload, ensure_ascii=False)
        details[key] = {
            "path": str(path),
            "exists": path.exists(),
            "has_mcpServers": bool(servers),
            "has_ms8_server": bool(server),
            "command_ok": _command_matches(command, expected_command),
            "args_ok": args == expected_args,
            "expected_args": list(expected_args),
            "verify_keys": list(verify_keys),
            "verify_keys_ok": all(verify_results.values()) if verify_results else True,
            "verify_results": verify_results,
            "legacy_path_found": LEGACY_PATH_HINT in raw_text,
        }
    overall_ok = all(
        d.get("exists")
        and d.get("has_mcpServers")
        and d.get("has_ms8_server")
        and d.get("command_ok")
        and d.get("args_ok")
        and d.get("verify_keys_ok")
        and not d.get("legacy_path_found")
        for d in details.values()
    )
    return {"ok": overall_ok, "target": target, "details": details}


def main() -> dict:
    return run()


if __name__ == "__main__":
    print(main())
