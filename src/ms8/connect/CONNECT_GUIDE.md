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

## Ledger v1 and Hybrid Retrieval v1

Normal MCP connection does not enable Memory Ledger v1 or Hybrid Retrieval v1. Existing clients remain on the legacy-compatible path unless the runtime has been migrated and all explicit gates are configured.

A representative MCP configuration section is:

```yaml
memory_core:
  workspace: /path/to/ms8-workspace
memory_ledger_v1:
  enabled: true
  retrieval_profile: hybrid-v1
  context_token_budget: 1200
  max_per_subject_predicate: 2
  hybrid:
    timezone: UTC
    max_claims: 12
    max_per_subject: 3
    max_per_predicate: 3
    graph_max_hops: 2
```

The MCP server process must also receive:

```text
MS8_MEMORY_LEDGER_V1=1
MS8_MEMORY_HYBRID_V1=1
```

The workspace must already contain:

- an authoritative runtime-format manifest selecting `ledger-v1`;
- a valid Ledger whose head matches the manifest;
- fresh SQLite, Search, FTS, Vector, and Graph projections for the same Ledger head.

The `query`, `context`, and `prepare_reply` tools preserve their existing required primary fields. Hybrid Retrieval adds optional, additive inputs:

- `purpose` on `query`;
- `explain` on `query`, `context`, and `prepare_reply`;
- `recorded_as_of`, `observed_as_of`, and `valid_at`;
- `realm_id` and `scope`.

An explicitly requested Ledger/Hybrid route fails closed when the Ledger, manifest, environment, or projections are invalid. It does not silently fall back to legacy retrieval. The compatibility adapter is read-only; `submit` and `batch_submit` do not gain Ledger-v1 write authority.

Full implementation and security boundaries are documented in [Hybrid Retrieval v1](../../../docs/HYBRID_RETRIEVAL_V1.md).

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
- Treat full Hybrid explain traces as sensitive diagnostics because they may include claim, Evidence, Decision, realm, scope, conflict, and provider-health metadata.
