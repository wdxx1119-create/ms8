# External Connect Profiles

You can add custom MCP client profiles without changing MS8 code.

Place YAML files in either:

- `src/ms8/connect/profiles/` (packaged defaults)
- `$MS8_HOME/connect/profiles/` (runtime override/custom)

Minimal schema:

```yaml
name: my_client
aliases: [my_client, myclient]
path: ~/.myclient/mcp.json
snippet_file: my_client_mcp.json
config_format: json
merge_strategy: upsert
verify_keys: [command, args]
args: [connect, status, --target, my_client]
env:
  MS8_AGENT_TARGET: my_client
```

Notes:

- `name` becomes the target key (`ms8 connect apply --target my_client`).
- Runtime profiles override packaged profiles with the same `name`.
- Unknown keys are ignored for safety.
