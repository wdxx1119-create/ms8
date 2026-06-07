from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import yaml

from .common import connect_root

logger = logging.getLogger(__name__)

SERVER_NAME = "ms8-memory"
LEGACY_PATH_HINT = "openclaw-memory-auto/mcp_server/mcp_server.py"


def _as_str_list(value: object) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value]
    return []


def _cherry_candidates() -> list[Path]:
    home = Path.home()
    return [
        home / "Library" / "Application Support" / "CherryStudio" / "mcp.json",
        home / "Library" / "Application Support" / "Cherry Studio" / "mcp.json",
        home / ".cherrystudio" / "mcp.json",
        home / ".config" / "cherrystudio" / "mcp.json",
    ]


def _resolve_cherry_path() -> Path:
    for candidate in _cherry_candidates():
        if candidate.exists():
            return candidate
    # Fallback default path when nothing is discovered on this host.
    return _cherry_candidates()[0]


def _vscode_global_storage_candidates(extension_ids: list[str], filename: str = "mcp.json") -> list[Path]:
    base = Path.home() / "Library" / "Application Support" / "Code" / "User" / "globalStorage"
    return [base / ext / filename for ext in extension_ids]


def _first_existing_or_fallback(candidates: list[Path], fallback: Path) -> Path:
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return fallback


def _cline_candidates() -> list[Path]:
    return [
        Path.home() / ".cline" / "mcp.json",
        Path.home() / ".config" / "cline" / "mcp.json",
        *_vscode_global_storage_candidates(["saoudrizwan.claude-dev", "cline.cline"], "mcp.json"),
    ]


def _roo_candidates() -> list[Path]:
    return [
        Path.home() / ".roo" / "mcp.json",
        Path.home() / ".roocode" / "mcp.json",
        Path.home() / ".config" / "roo" / "mcp.json",
        *_vscode_global_storage_candidates(["rooveterinaryinc.roo-cline", "rooveterinaryinc.roo-code"], "mcp.json"),
    ]


def _continue_candidates() -> list[Path]:
    return [
        Path.home() / ".continue" / "mcp.json",
        Path.home() / ".config" / "continue" / "mcp.json",
        *_vscode_global_storage_candidates(["continue.continue"], "mcp.json"),
    ]


def _resolve_cline_path() -> Path:
    return _first_existing_or_fallback(_cline_candidates(), _cline_candidates()[0])


def _resolve_roo_path() -> Path:
    return _first_existing_or_fallback(_roo_candidates(), _roo_candidates()[0])


def _resolve_continue_path() -> Path:
    return _first_existing_or_fallback(_continue_candidates(), _continue_candidates()[0])


def _resolve_generic_json_path() -> Path:
    return Path.home() / ".ms8" / "connect" / "generic_mcp.json"


def _codex_candidates() -> list[Path]:
    home = Path.home()
    return [
        home / ".codex" / "config.toml",
    ]


def _resolve_codex_path() -> Path:
    return _first_existing_or_fallback(_codex_candidates(), _codex_candidates()[0])


def _claude_code_candidates() -> list[Path]:
    home = Path.home()
    return [
        home / ".claude.json",
        home / ".claude" / "settings.json",
    ]


def _resolve_claude_code_path() -> Path:
    return _first_existing_or_fallback(_claude_code_candidates(), _claude_code_candidates()[0])

BUILTIN_AGENT_PROFILES: dict[str, dict[str, object]] = {
    "claude_desktop": {
        "aliases": ("claude", "claude_desktop"),
        "path": Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
        "snippet_file": "claude_desktop_config.json",
        "config_format": "json",
        "merge_strategy": "upsert",
        "verify_keys": ("command", "args"),
    },
    "cursor": {
        "aliases": ("cursor",),
        "path": Path.home() / ".cursor" / "mcp.json",
        "snippet_file": "cursor_mcp.json",
        "config_format": "json",
        "merge_strategy": "upsert",
        "verify_keys": ("command", "args"),
    },
    "windsurf": {
        "aliases": ("windsurf",),
        "path": Path.home() / ".windsurf" / "mcp.json",
        "snippet_file": "windsurf_mcp.json",
        "config_format": "json",
        "merge_strategy": "upsert",
        "verify_keys": ("command", "args"),
    },
    "openclaw": {
        "aliases": ("openclaw", "open_claw"),
        "path": Path.home() / ".openclaw" / "mcp.json",
        "snippet_file": "openclaw_mcp.json",
        "args": ("connect", "status", "--target", "openclaw"),
        "env": {"MS8_AGENT_TARGET": "openclaw"},
        "config_format": "json",
        "merge_strategy": "upsert",
        "verify_keys": ("command", "args", "env.MS8_AGENT_TARGET"),
    },
    "hermes": {
        "aliases": ("hermes", "hermes_agent"),
        "path": Path.home() / ".hermes" / "mcp.json",
        "snippet_file": "hermes_mcp.json",
        "args": ("connect", "status", "--target", "hermes"),
        "env": {"MS8_AGENT_TARGET": "hermes"},
        "config_format": "json",
        "merge_strategy": "upsert",
        "verify_keys": ("command", "args", "env.MS8_AGENT_TARGET"),
    },
    "cline": {
        "aliases": ("cline", "claude_dev"),
        "path_resolver": _resolve_cline_path,
        "snippet_file": "cline_mcp.json",
        "args": ("connect", "status", "--target", "cline"),
        "env": {"MS8_AGENT_TARGET": "cline"},
        "config_format": "json",
        "merge_strategy": "upsert",
        "verify_keys": ("command", "args", "env.MS8_AGENT_TARGET"),
    },
    "roo": {
        "aliases": ("roo", "roo_code", "roo_cline"),
        "path_resolver": _resolve_roo_path,
        "snippet_file": "roo_mcp.json",
        "args": ("connect", "status", "--target", "roo"),
        "env": {"MS8_AGENT_TARGET": "roo"},
        "config_format": "json",
        "merge_strategy": "upsert",
        "verify_keys": ("command", "args", "env.MS8_AGENT_TARGET"),
    },
    "continue": {
        "aliases": ("continue", "continue_dev"),
        "path_resolver": _resolve_continue_path,
        "snippet_file": "continue_mcp.json",
        "args": ("connect", "status", "--target", "continue"),
        "env": {"MS8_AGENT_TARGET": "continue"},
        "config_format": "json",
        "merge_strategy": "upsert",
        "verify_keys": ("command", "args", "env.MS8_AGENT_TARGET"),
    },
    "cherry_studio": {
        "aliases": ("cherry", "cherry_studio", "cherrystudio"),
        "path_resolver": _resolve_cherry_path,
        "snippet_file": "cherry_studio_mcp.json",
        "args": ("connect", "status", "--target", "cherry_studio"),
        "env": {"MS8_AGENT_TARGET": "cherry_studio"},
        "config_format": "json",
        "merge_strategy": "upsert",
        "verify_keys": ("command", "args", "env.MS8_AGENT_TARGET"),
    },
    "generic_json": {
        "aliases": ("generic_json", "generic", "export"),
        "path_resolver": _resolve_generic_json_path,
        "snippet_file": "generic_mcp.json",
        "args": ("connect", "status"),
        "config_format": "json",
        "merge_strategy": "replace",
        "verify_keys": ("command", "args"),
    },
    "codex": {
        "aliases": ("codex", "codex_desktop"),
        "path_resolver": _resolve_codex_path,
        "snippet_file": "codex_mcp.toml",
        "config_format": "toml",
        "merge_strategy": "upsert",
        "verify_keys": ("command", "args"),
    },
    "claude_code": {
        "aliases": ("claude_code", "claude-code", "claudecode"),
        "path_resolver": _resolve_claude_code_path,
        "snippet_file": "claude_code_mcp.json",
        "config_format": "json",
        "merge_strategy": "upsert",
        "verify_keys": ("command", "args"),
    },
}


def _profile_dirs() -> list[Path]:
    pkg_dir = Path(__file__).resolve().parents[1] / "profiles"
    runtime_dir = connect_root() / "profiles"
    return [pkg_dir, runtime_dir]


def _sanitize_name(raw: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in str(raw).strip().lower())


def _load_external_profiles() -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    allowed_keys = {"aliases", "path", "snippet_file", "args", "env", "config_format", "merge_strategy", "verify_keys"}
    for root in _profile_dirs():
        if not root.exists():
            continue
        for yml in sorted(list(root.glob("*.yaml")) + list(root.glob("*.yml"))):
            try:
                payload = yaml.safe_load(yml.read_text(encoding="utf-8"))
            except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
                logger.debug("Failed to load external profile file %s: %s", yml, exc)
                continue
            if not isinstance(payload, dict):
                continue
            name = _sanitize_name(str(payload.get("name", yml.stem)))
            if not name:
                continue
            row: dict[str, object] = {}
            for k in allowed_keys:
                if k in payload:
                    row[k] = payload[k]
            if "path" in row:
                row["path"] = Path(str(row["path"])).expanduser()
            if "aliases" in row and isinstance(row["aliases"], list):
                row["aliases"] = tuple(str(x) for x in row["aliases"])
            if "args" in row and isinstance(row["args"], list):
                row["args"] = tuple(str(x) for x in row["args"])
            if "verify_keys" in row and isinstance(row["verify_keys"], list):
                row["verify_keys"] = tuple(str(x) for x in row["verify_keys"])
            row.setdefault("aliases", (name,))
            row.setdefault("snippet_file", f"{name}_mcp.json")
            row.setdefault("config_format", "json")
            row.setdefault("merge_strategy", "upsert")
            row.setdefault("verify_keys", ("command", "args"))
            out[name] = row
    return out


def _agent_profiles() -> dict[str, dict[str, object]]:
    merged = dict(BUILTIN_AGENT_PROFILES)
    merged.update(_load_external_profiles())
    return merged


def supported_targets() -> list[str]:
    return sorted(_agent_profiles().keys())


def normalize_target(target: str | None) -> str:
    profiles = _agent_profiles()
    raw = str(target or "all").strip().lower()
    if raw in {"", "all", "*"}:
        return "all"
    for name, profile in profiles.items():
        aliases = tuple(_as_str_list(profile.get("aliases", ())))
        if raw == name or raw in aliases:
            return name
    raise ValueError(f"unsupported_target:{target}")


def selected_targets(target: str | None = "all") -> list[str]:
    normalized = normalize_target(target)
    if normalized == "all":
        return supported_targets()
    return [normalized]


def target_paths(target: str | None = "all") -> dict[str, Path]:
    profiles = _agent_profiles()
    out: dict[str, Path] = {}
    for name in selected_targets(target):
        profile = profiles[name]
        resolver = profile.get("path_resolver")
        if callable(resolver):
            resolved = resolver()
            out[name] = Path(resolved)
            continue
        out[name] = Path(profile["path"])  # type: ignore[arg-type]
    return out


def snippet_paths(target: str | None = "all") -> dict[str, str]:
    profiles = _agent_profiles()
    out: dict[str, str] = {}
    for name in selected_targets(target):
        out[name] = str(profiles[name]["snippet_file"])
    return out


def target_profile(target: str) -> dict[str, object]:
    profiles = _agent_profiles()
    normalized = normalize_target(target)
    if normalized == "all":
        raise ValueError("target_profile requires a concrete target")
    return dict(profiles[normalized])


def target_discovery(target: str | None = "all") -> dict[str, dict[str, object]]:
    profiles = _agent_profiles()
    out: dict[str, dict[str, object]] = {}
    for name in selected_targets(target):
        profile = profiles[name]
        if name == "cherry_studio":
            candidates = [str(p) for p in _cherry_candidates()]
            resolved = str(_resolve_cherry_path())
            out[name] = {
                "strategy": "candidate_scan_then_fallback",
                "candidates": candidates,
                "resolved": resolved,
                "resolved_exists": Path(resolved).exists(),
                "config_format": str(profile.get("config_format", "json")),
                "merge_strategy": str(profile.get("merge_strategy", "upsert")),
                "verify_keys": _as_str_list(profile.get("verify_keys", ())),
            }
        elif name == "cline":
            candidates = [str(p) for p in _cline_candidates()]
            resolved = str(_resolve_cline_path())
            out[name] = {
                "strategy": "candidate_scan_then_fallback",
                "candidates": candidates,
                "resolved": resolved,
                "resolved_exists": Path(resolved).exists(),
                "config_format": str(profile.get("config_format", "json")),
                "merge_strategy": str(profile.get("merge_strategy", "upsert")),
                "verify_keys": _as_str_list(profile.get("verify_keys", ())),
            }
        elif name == "roo":
            candidates = [str(p) for p in _roo_candidates()]
            resolved = str(_resolve_roo_path())
            out[name] = {
                "strategy": "candidate_scan_then_fallback",
                "candidates": candidates,
                "resolved": resolved,
                "resolved_exists": Path(resolved).exists(),
                "config_format": str(profile.get("config_format", "json")),
                "merge_strategy": str(profile.get("merge_strategy", "upsert")),
                "verify_keys": _as_str_list(profile.get("verify_keys", ())),
            }
        elif name == "continue":
            candidates = [str(p) for p in _continue_candidates()]
            resolved = str(_resolve_continue_path())
            out[name] = {
                "strategy": "candidate_scan_then_fallback",
                "candidates": candidates,
                "resolved": resolved,
                "resolved_exists": Path(resolved).exists(),
                "config_format": str(profile.get("config_format", "json")),
                "merge_strategy": str(profile.get("merge_strategy", "upsert")),
                "verify_keys": _as_str_list(profile.get("verify_keys", ())),
            }
        elif name == "codex":
            candidates = [str(p) for p in _codex_candidates()]
            resolved = str(_resolve_codex_path())
            out[name] = {
                "strategy": "candidate_scan_then_fallback",
                "candidates": candidates,
                "resolved": resolved,
                "resolved_exists": Path(resolved).exists(),
                "config_format": str(profile.get("config_format", "toml")),
                "merge_strategy": str(profile.get("merge_strategy", "upsert")),
                "verify_keys": _as_str_list(profile.get("verify_keys", ())),
            }
        elif name == "claude_code":
            candidates = [str(p) for p in _claude_code_candidates()]
            resolved = str(_resolve_claude_code_path())
            out[name] = {
                "strategy": "candidate_scan_then_fallback",
                "candidates": candidates,
                "resolved": resolved,
                "resolved_exists": Path(resolved).exists(),
                "config_format": str(profile.get("config_format", "json")),
                "merge_strategy": str(profile.get("merge_strategy", "upsert")),
                "verify_keys": _as_str_list(profile.get("verify_keys", ())),
            }
        else:
            resolved = str(target_paths(name)[name])
            out[name] = {
                "strategy": "fixed_path",
                "candidates": [resolved],
                "resolved": resolved,
                "resolved_exists": Path(resolved).exists(),
                "config_format": str(profile.get("config_format", "json")),
                "merge_strategy": str(profile.get("merge_strategy", "upsert")),
                "verify_keys": _as_str_list(profile.get("verify_keys", ())),
            }
    return out


def supported_target_matrix() -> dict[str, dict[str, Any]]:
    profiles = _agent_profiles()
    matrix: dict[str, dict[str, Any]] = {}
    discovery = target_discovery("all")
    for name in supported_targets():
        profile = profiles[name]
        matrix[name] = {
            "aliases": _as_str_list(profile.get("aliases", ())),
            "snippet_file": str(profile.get("snippet_file", "")),
            "config_format": str(profile.get("config_format", "json")),
            "merge_strategy": str(profile.get("merge_strategy", "upsert")),
            "verify_keys": _as_str_list(profile.get("verify_keys", ())),
            "discovery": discovery.get(name, {}),
        }
    return matrix


def payload_for_target(target: str) -> dict:
    profiles = _agent_profiles()
    normalized = normalize_target(target)
    if normalized == "all":
        raise ValueError("payload_for_target requires a concrete target")
    profile = profiles[normalized]
    command, command_prefix_args = expected_command_signature(normalized)
    route_args = expected_route_args(normalized)
    args = tuple(command_prefix_args) + route_args
    env_raw = profile.get("env", {})
    env = dict(env_raw) if isinstance(env_raw, dict) else {}
    src_root = Path(__file__).resolve().parents[3]
    if src_root.exists() and "PYTHONPATH" not in env:
        env["PYTHONPATH"] = str(src_root)
    server_payload: dict[str, object] = {
        "command": command,
        "args": list(args),
    }
    if env:
        server_payload["env"] = env
    if str(profile.get("config_format", "json")) == "toml":
        return {
            "mcp_servers": {
                SERVER_NAME: server_payload,
            }
        }
    return {
        "mcpServers": {
            SERVER_NAME: server_payload
        }
    }


def payload() -> dict:
    return payload_for_target("claude_desktop")


def expected_command_signature(target: str | None = None) -> tuple[str, tuple[str, ...]]:
    """
    Return executable command + required prefix args before target-specific route args.
    - Prefer plain `ms8` if available in PATH.
    - Fallback to current Python interpreter: `python -m ms8`.
    """
    cmd_override = str(os.environ.get("MS8_MCP_COMMAND", "")).strip()
    if cmd_override:
        cmd = cmd_override
        prefix = tuple(str(os.environ.get("MS8_MCP_COMMAND_PREFIX_ARGS", "")).split()) if os.environ.get("MS8_MCP_COMMAND_PREFIX_ARGS") else ()
        return (cmd, prefix)
    ms8_bin = shutil.which("ms8")
    if ms8_bin:
        # Keep process anchored to a Python interpreter so we can start stdio MCP server module directly.
        return (sys.executable, ("-m", "ms8.connect.mcp_server.stdio_server"))
    return (sys.executable, ("-m", "ms8.connect.mcp_server.stdio_server"))


def expected_route_args(target: str | None = None) -> tuple[str, ...]:
    _ = target
    return ()
