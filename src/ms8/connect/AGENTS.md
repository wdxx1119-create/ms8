# MS8 MCP Agent Template

This file is an intentionally minimal template for MCP-capable tools.
You can copy sections below and customize for your own workflow.

## 1) Core Principle

- Keep one canonical server id: `ms8-memory`
- Do not overwrite unrelated MCP servers in your client config
- Prefer `ms8 connect apply --target <target>` for managed setup

## 2) Minimal Generic Template (JSON)

```json
{
  "mcpServers": {
    "ms8-memory": {
      "command": "python3",
      "args": ["-m", "ms8.connect.mcp_server.stdio_server"],
      "env": {
        "MS8_AGENT_TARGET": "generic"
      }
    }
  }
}
```

## 3) Minimal Generic Template (TOML)

```toml
[mcp_servers.ms8-memory]
command = "python3"
args = ["-m", "ms8.connect.mcp_server.stdio_server"]

[mcp_servers.ms8-memory.env]
MS8_AGENT_TARGET = "generic"
```

## 4) Suggested Agent Behavior (You Customize)

You can define your own policy for when the agent should call MS8 tools:

- Before answering: call `context`
- When user states stable facts/preferences/decisions: call `submit`
- For multiple durable items in one turn: call `batch_submit`
- Periodically check health: call `status`

## 5) Common Pitfalls

- Wrong Python path or venv mismatch
- Client config exists but does not include `ms8-memory`
- Legacy path references from old projects
- Read-only environment preventing config write

## 6) Recommended Validation

1. `ms8 connect verify --target <target>`
2. MCP client calls:
   - `status`
   - `context` (query test)
   - `submit` (write test)

If all three pass, integration is healthy.
