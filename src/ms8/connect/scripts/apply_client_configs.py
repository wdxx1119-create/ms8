from __future__ import annotations

import json
import logging
from pathlib import Path

import tomllib

from ms8.connect.scripts.client_config import LEGACY_PATH_HINT, snippet_paths, target_paths, target_profile
from ms8.connect.scripts.common import connect_root

logger = logging.getLogger(__name__)


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        logger.debug("Failed to read JSON %s: %s", path, exc)
        return {}
    return obj if isinstance(obj, dict) else {}


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_toml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        obj = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, tomllib.TOMLDecodeError) as exc:
        logger.debug("Failed to read TOML %s: %s", path, exc)
        return {}
    return obj if isinstance(obj, dict) else {}


def _write_toml_server_upsert(path: Path, server_payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    base = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = base.splitlines()

    server_header = "[mcp_servers.ms8-memory]"
    env_header = "[mcp_servers.ms8-memory.env]"
    cmd = str(server_payload.get("command", ""))
    args = server_payload.get("args", []) if isinstance(server_payload.get("args", []), list) else []
    env = server_payload.get("env", {}) if isinstance(server_payload.get("env", {}), dict) else {}

    block = [
        server_header,
        f'command = "{cmd}"',
        "args = [" + ", ".join(json.dumps(str(a), ensure_ascii=False) for a in args) + "]",
    ]
    if env:
        block.append(env_header)
        for k, v in env.items():
            block.append(f"{k} = {json.dumps(str(v), ensure_ascii=False)}")

    start = None
    end = None
    for i, line in enumerate(lines):
        if line.strip() == server_header:
            start = i
            end = len(lines)
            for j in range(i + 1, len(lines)):
                stripped = lines[j].strip()
                if stripped.startswith("[") and stripped.endswith("]") and stripped not in {env_header}:
                    end = j
                    break
            break
    if start is not None and end is not None:
        new_lines = lines[:start] + block + lines[end:]
    else:
        new_lines = lines + ([""] if lines else []) + block
    path.write_text("\n".join(new_lines).rstrip() + "\n", encoding="utf-8")


def _select_source(primary: Path, alternate: Path) -> Path:
    if primary.suffix.lower() == ".toml":
        return primary if primary.exists() else alternate
    if primary.exists():
        p1 = _read_json(primary)
        if isinstance(p1.get("mcpServers"), dict) and p1.get("mcpServers"):
            return primary
    return alternate


def _targets(target: str = "all") -> dict[str, Path]:
    return target_paths(target)


def run(target: str = "all") -> dict:
    root = connect_root()
    snippets = root / "runtime" / "client_snippets"
    local_snippets = Path.cwd() / ".ms8" / "connect" / "runtime" / "client_snippets"
    snippet_rel = snippet_paths(target)
    mapping_primary = {name: snippets / rel for name, rel in snippet_rel.items()}
    mapping_local = {name: local_snippets / rel for name, rel in snippet_rel.items()}
    out = {}
    hints: list[str] = []
    for key, dest in target_paths(target).items():
        profile = target_profile(key)
        config_format = str(profile.get("config_format", "json"))
        src = _select_source(mapping_primary[key], mapping_local[key])
        if not src.exists():
            out[key] = {"ok": False, "reason": "snippet_missing", "path": str(src)}
            continue
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if config_format == "toml":
                previous_payload = _read_toml(dest)
                had_legacy_path = LEGACY_PATH_HINT in (dest.read_text(encoding="utf-8") if dest.exists() else "")
                payload = _read_toml(src)
                server_payload = payload.get("mcp_servers", {}).get("ms8-memory", {}) if isinstance(payload.get("mcp_servers", {}), dict) else {}
                if not isinstance(server_payload, dict):
                    server_payload = {}
                _write_toml_server_upsert(dest, server_payload)
                first_write = not previous_payload
            else:
                previous_payload = _read_json(dest)
                had_legacy_path = LEGACY_PATH_HINT in json.dumps(previous_payload, ensure_ascii=False)
                payload = _read_json(src)
                _write_json(dest, payload)
                first_write = not previous_payload
            if key == "cherry_studio":
                if first_write:
                    hints.append(
                        "Cherry Studio: MCP config created at "
                        f"{dest}. Open Cherry Studio MCP settings and import/reload this server if needed."
                    )
                else:
                    hints.append(
                        "Cherry Studio: MCP config updated at "
                        f"{dest}. Reload MCP servers in Cherry Studio to apply changes."
                    )
            out[key] = {
                "ok": True,
                "target": str(dest),
                "migrated_legacy_path": had_legacy_path,
                "first_write": first_write,
            }
        except (OSError, TypeError, ValueError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
            out[key] = {"ok": False, "error": str(exc), "target": str(dest)}
    return {
        "ok": all(v.get("ok", False) for v in out.values()),
        "target": target,
        "details": out,
        "hints": hints,
    }


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
