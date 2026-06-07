from __future__ import annotations

import json

from .client_config import payload_for_target, snippet_paths, target_profile
from .common import connect_root


def run(target: str = "all") -> dict:
    root = connect_root()
    snippets = root / "runtime" / "client_snippets"
    snippets.mkdir(parents=True, exist_ok=True)
    rel_map = snippet_paths(target)
    files = [snippets / rel for rel in rel_map.values()]
    for name, rel in rel_map.items():
        generated_payload = payload_for_target(name)
        profile = target_profile(name)
        p = snippets / rel
        if str(profile.get("config_format", "json")) == "toml":
            server = generated_payload.get("mcp_servers", {}).get("ms8-memory", {})
            command = str(server.get("command", ""))
            args = server.get("args", []) if isinstance(server.get("args", []), list) else []
            env = server.get("env", {}) if isinstance(server.get("env", {}), dict) else {}
            lines = [
                "[mcp_servers.ms8-memory]",
                f'command = "{command}"',
                "args = [" + ", ".join(json.dumps(str(a), ensure_ascii=False) for a in args) + "]",
            ]
            if env:
                lines.append("[mcp_servers.ms8-memory.env]")
                for k, v in env.items():
                    lines.append(f"{k} = {json.dumps(str(v), ensure_ascii=False)}")
            p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        else:
            p.write_text(json.dumps(generated_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "target": target, "files": [str(p) for p in files]}


def main() -> dict:
    return run()


if __name__ == "__main__":
    print(main())
