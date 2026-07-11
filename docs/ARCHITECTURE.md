# MS8 Architecture

This document describes the current Alpha architecture of MS8. It is an implementation guide, not a promise that every internal module or file format is already stable.

## Design goals

MS8 is organized around five constraints:

1. **Local-first storage**: the primary memory runtime lives in a user-controlled local directory.
2. **Governed writes and recall**: records carry scope, authority, sensitivity and capability flags; unsafe states are reviewable or quarantined instead of silently injected.
3. **Recoverability**: backup, dry-run, audit and repair paths are preferred over irreversible automation.
4. **Optional model enhancement**: local or external model providers may improve retrieval, but the basic record and rule paths must remain usable when those providers are unavailable.
5. **Verifiable distribution**: the installed wheel, packaged MCP resources and isolated runtime paths are exercised in CI on Windows, macOS and Linux.

## System context

```text
User / AI client
      |
      +-- CLI (`ms8 ...`)
      +-- MCP client configuration
      +-- local files authorized for Absorb
      |
      v
Command and adapter layer
      |
      +-- runtime / lifecycle / doctor
      +-- connect and MCP resources
      +-- Absorb scanner, parser and repository
      |
      v
Governance and engine layer
      |
      +-- canonical record policy
      +-- admission, review and quarantine
      +-- MemoryCore engine
      +-- retrieval, graph and index services
      |
      v
User-controlled local runtime
      +-- canonical JSONL records
      +-- SQLite and index stores
      +-- knowledge graph
      +-- health, audit and repair records
      +-- backups and Absorb staging data
```

## Main entry points

### CLI

The `ms8` console script maps to `ms8.cli:main`. The CLI is the supported public operational surface for installation checks, memory write/search, doctor, dashboard, maintenance, MCP connection and Absorb workflows.

CLI commands should resolve paths through `ms8.paths` and use the same governance and engine services as other adapters. Commands that can remove or rewrite data should retain preview or dry-run behavior where available.

### Connect and MCP

`src/ms8/connect/` contains packaged MCP configuration, adapter registry data and connection helpers. Package validation checks that the wheel contains:

- `connect/config/mcp_config.yaml`
- `connect/adapter_registry/adapters.json`

Adapters must not bypass the canonical write and recall policies merely because the caller is an AI tool.

### Absorb

`src/ms8/absorb/` handles explicitly authorized local material. Absorb has a separate staging repository so file discovery, parsing, risk decisions, review and submission can be audited before a record enters the main memory store.

Parsing a document is not equivalent to accepting it as memory. Submission to MS8 remains a distinct governed transition.

## Runtime path resolution

`ms8.paths` is the canonical path resolver.

Path precedence:

1. Explicit environment variables such as `MS8_HOME`, `MS8_DATA_DIR`, `MS8_CONFIG_DIR` and `MS8_LOG_DIR`.
2. Existing runtime data under `~/.ms8` or the legacy `~/.ms8_runtime`, with the directory containing the stronger data markers preferred.
3. A fresh `~/.ms8` directory.

Tests and examples should set all runtime path variables to a temporary directory before importing or invoking MS8. This prevents accidental access to a developer's real data.

## Engine and canonical records

`ms8.engine.MemoryCoreEngine` is the unified engine wrapper. It initializes the bundled `MemoryCore`, exposes status information and identifies the canonical records file. The normal record location is:

```text
<MS8_HOME>/memory/auto_memory_records.jsonl
```

If the core cannot be used, the wrapper has an explicit local fallback path that still calls `append_canonical_record`; fallback writes do not skip record validation.

`ms8.record_policy` defines the canonical record contract, including:

- normalized text
- category and lifecycle status
- source and creation time
- scope, authority and sensitivity
- `can_recall`, `can_inject` and `can_act_on`
- admission metadata

The engine applies recall and injection policy separately. A record can be locally stored yet excluded from normal recall or automatic injection.

## Storage roles

MS8 uses multiple local stores with different authority levels:

- **Canonical memory JSONL**: authoritative append-oriented record stream.
- **Knowledge graph and search indexes**: derived retrieval structures; they must be rebuildable from authoritative data or audited sources.
- **SQLite stores**: structured indexes, operational repositories and Absorb staging state.
- **Quarantine and review data**: records that failed validation or require a decision.
- **Health and audit files**: doctor, governance, repair and maintenance evidence.
- **Backups**: recovery material; never assume an index alone is a sufficient backup.

See [DATA_MODEL.md](DATA_MODEL.md) for file and field details.

## Governance flow

A simplified governed write path is:

```text
input
  -> normalize and classify
  -> apply admission and policy checks
  -> assign scope / authority / sensitivity
  -> accepted, pending review, or quarantined
  -> append canonical record when allowed
  -> update or rebuild derived retrieval structures
```

A simplified recall path is:

```text
query
  -> retrieve candidates
  -> reject expired, revoked, quarantined or superseded records
  -> apply sensitivity, authority and scope rules
  -> return recallable records
  -> inject only records whose injection policy also allows it
```

Important separation:

- storage does not imply recall
- recall does not imply injection
- injection does not grant action authority

## Self-check, maintenance and observability

The runtime creates health, repair-audit, quarantine and maintenance-window files. Self-check execution uses a non-blocking file lock:

- POSIX systems use `fcntl`
- Windows uses `msvcrt`

This prevents concurrent self-check runs while preserving stale-run recovery behavior.

`doctor`, dashboard and maintenance commands should report the difference between a hard failure, a degraded optional capability and an informational warning. Logs and reports must avoid exposing memory content, credentials, personal paths or PII.

## Network boundaries

The primary record store is local, but not every optional feature is offline. Network access may occur when a user explicitly configures model providers, health-check endpoints, package installation or other external integrations.

Code and documentation should therefore state the exact feature that performs network access instead of making a blanket claim that every MS8 operation is offline.

## Packaging and CI boundaries

The CI system verifies:

- Python 3.10–3.13 tests
- Ruff and mypy
- an 80% line-coverage baseline
- wheel and source-distribution metadata
- clean-room installation
- installed-wheel smoke on Windows
- isolated release tests on macOS and Ubuntu
- packaged MCP resources and Absorb parsing
- CodeQL and dependency review

Release workflows read the project version from `pyproject.toml`; version-specific filenames should not be duplicated in workflow source.

## Extension rules

A new adapter, parser or provider should:

1. keep path handling inside the unified runtime resolver
2. avoid writing canonical records directly
3. preserve source and provenance metadata
4. expose failure and degraded behavior clearly
5. avoid logging sensitive payloads
6. include isolated tests that do not use real user data
7. document any network access or external dependency

## Known Alpha constraints

- Internal APIs and data layouts may still change.
- Compatibility and migration policy is not yet a stable public API contract.
- Some derived stores have more mature rebuild and consistency tooling than others.
- Optional provider behavior depends on third-party software and services.
- Multi-user hosted collaboration is outside the current local-first architecture.

Security assumptions and residual risks are documented in [THREAT_MODEL.md](THREAT_MODEL.md).
