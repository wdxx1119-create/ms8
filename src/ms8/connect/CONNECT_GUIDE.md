# MS8 MCP Connection Guide

This guide provides two paths for MCP-capable tools:

- Path A: Manual connection
- Path B: Agent-assisted automatic connection

---

## Path A: Manual Connection

Use this when you want explicit control.

1. Generate snippet:

```bash
ms8 connect generate --target generic_json
```

2. Import snippet into your MCP client config.

3. Verify:

```bash
ms8 connect verify --target <target>
```

4. Smoke check:

```bash
ms8 connect smoke --target <target>
```

---

## Path B: Agent-Assisted Automatic Connection

Use this for quickest onboarding.

1. Bootstrap:

```bash
ms8 connect bootstrap --target all
```

2. Optional repair pass:

```bash
ms8 connect apply --target all
ms8 connect verify --target all
```

3. Read first-install report:

- `$MS8_HOME/connect/runtime/first_install_connect_report.json`
- `$MS8_HOME/connect/runtime/first_install_connect_report.txt`

---

## Helpful Commands

- List supported targets:

```bash
ms8 connect list-targets
```

- Compact target/path view:

```bash
ms8 connect list-targets --compact
```

- Rollback only `ms8-memory` server entry:

```bash
ms8 connect rollback --target <target>
```

---

## Safety Notes

- Default rollback is selective: it removes only `ms8-memory` entry.
- It does not delete unrelated MCP servers.
- Use full-file delete only when explicitly intended.
