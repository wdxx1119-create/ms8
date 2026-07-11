# MS8 Architecture

This document describes the current MS8 implementation on `main`. It is an implementation guide, not a promise that every internal interface is stable during Alpha.

## 1. Design goals

MS8 is organized around five constraints:

1. **Local-first ownership** — primary memory data lives in directories controlled by the user.
2. **Governed writes and reads** — storage, recall, context injection, and automation must respect record state, permissions, source metadata, and risk decisions.
3. **Recoverability** — append-oriented records, backups, dry-runs, audit events, and rebuildable indexes are preferred over opaque irreversible changes.
4. **Optional intelligence** — local or remote model providers may improve extraction and retrieval, but core storage and rule-based retrieval have explicit degraded paths.
5. **Tool neutrality** — CLI and MCP clients share the same underlying memory and governance surfaces rather than maintaining separate memory silos.

## 2. Top-level components

```text
User / automation / MCP client
            |
            v
        CLI and MCP
            |
            v
     Runtime orchestration
       /       |        \
      v        v         v
 governance  memory    Absorb
   policy     engine    pipeline
      |        |         |
      +--------+---------+
               |
               v
      local records and indexes
     JSONL / SQLite / graph / audit
```

### CLI

`src/ms8/cli.py` is the command router for lifecycle, memory, diagnostics, MCP connection, Absorb, backup, reset, and maintenance operations. Command modules should call shared runtime or engine functions rather than implement a second persistence path.

### Runtime orchestration

`src/ms8/runtime.py` prepares runtime directories, coordinates record files, and exposes higher-level operations used by the CLI. `src/ms8/paths.py` centralizes path selection and contains protections against silently splitting data between multiple legacy/default roots.

### Memory engine

`src/ms8/engine.py` is the public engine wrapper. The larger implementation lives under `src/ms8/engine_core/`, including:

- `core.py` — engine composition and main memory operations.
- `governance.py` and admission compatibility modules — governance decisions and compatibility surfaces.
- `sqlite_store.py` and `file_store.py` — structured and file-backed persistence helpers.
- `whoosh_search.py` and `semantic_search.py` — lexical and optional semantic retrieval.
- `knowledge_graph.py` — graph persistence, isolation checks, and rebuild operations.
- `security/` — encryption, shadow/audit, control gates, recovery, and quarantine mechanisms.
- `maintenance/` — self-check, repair planning, cleanup, and health reporting.

### Canonical record policy

`src/ms8/record_policy.py` defines canonical record normalization and policy helpers. A memory record is not treated as free text alone: state, source, permissions, sensitivity, expiry, review, and provenance can affect whether it is eligible for recall or context injection.

### MCP connection layer

`src/ms8/connect/` contains packaged MCP configuration, the adapter registry, client bootstrap/verification commands, and the MCP server implementation. The MCP service calls the same memory interfaces as local commands and must not bypass governance rules.

Packaged resources are validated from installed wheels so source-tree-only files cannot accidentally become a hidden runtime dependency.

### Absorb

`src/ms8/absorb/` implements explicitly authorized local-material ingestion:

1. Scan an allowed source.
2. Parse a supported file into an intermediate document.
3. Apply governance decisions and exclusions.
4. Store staging, review, quarantine, and source events.
5. Submit approved material through the governed memory path.

Absorb has its own SQLite repository and audit/event data. Automatic final writes remain conservative; parsed content is not equivalent to an approved canonical memory.

## 3. Runtime path model

The runtime root is resolved from explicit environment/configuration first and otherwise falls back to the user's default MS8 directory. Important environment variables include:

- `MS8_HOME`
- `MS8_DATA_DIR`
- `MS8_CONFIG_DIR`
- `MS8_LOG_DIR`

Tests and examples must set these variables to temporary directories before importing runtime-sensitive modules. They must never rely on or modify a developer's real home directory.

The runtime creates or maintains locations for records, configuration, logs, indexes, reports, backups, quarantine data, and service state. Not every derived file is authoritative; several indexes and reports can be regenerated from canonical records and audit inputs.

## 4. Governed write flow

A typical memory write follows this conceptual sequence:

```text
input
  -> normalization
  -> source/provenance attachment
  -> admission and risk checks
  -> canonical record construction
  -> append/persist
  -> index and graph updates
  -> audit/health signals
```

Important properties:

- Rejected or quarantined material is not silently promoted to an active record.
- Candidate, pending-review, inactive, expired, or source-restricted records have different retrieval eligibility.
- Sensitive fields and credentials must not be echoed into logs or workflow artifacts.
- When a secondary index update fails, the canonical source should remain recoverable and the index should be rebuildable.

## 5. Recall and context flow

A typical read follows this conceptual sequence:

```text
query
  -> lexical/structured candidate retrieval
  -> optional semantic enhancement
  -> record-policy filtering
  -> ranking and limits
  -> response or context material
```

Retrieval is therefore not a raw dump of every stored row. Eligibility can depend on record status, expiry, quarantine, review state, sensitivity, source permissions, and the requesting surface.

Model providers are enhancement layers. Provider unavailability should result in an explicit degraded mode rather than changing ownership of the canonical local data.

## 6. Storage responsibilities

| Storage | Typical responsibility | Authoritative? |
|---|---|---|
| JSONL record files | Append-oriented memory and event history | Canonical for the corresponding record stream |
| SQLite stores | Structured lookup, operational state, Absorb repository, graph/index metadata | Depends on store; some are rebuildable |
| Whoosh/semantic indexes | Retrieval acceleration | Rebuildable |
| Knowledge graph database | Entity/relation retrieval and graph state | Rebuildable from supported sources where tooling permits |
| Reports and dashboards | Health and operational summaries | Derived |
| Backups | Recovery snapshots | Recovery source, not a live write target |
| Quarantine | Isolated material requiring review or rejection handling | Must not be treated as active memory |

The exact file schema is documented in [DATA_MODEL.md](DATA_MODEL.md).

## 7. Diagnostics and maintenance

`ms8 doctor`, dashboard output, self-check runners, and repair planners expose health without assuming every warning is fatal. Maintenance operations should support preview or dry-run when they can remove, reset, migrate, or rewrite data.

Self-check locking is platform-specific behind a narrow interface:

- POSIX uses `fcntl.flock`.
- Windows uses `msvcrt.locking`.

The purpose is identical: avoid concurrent self-check runs while preserving stale-run reporting and cleanup.

## 8. Packaging and release architecture

The release path builds both wheel and source distribution, verifies metadata and filenames, installs artifacts into clean environments, checks packaged resources, and runs isolated smoke tests on supported platforms.

Current CI validates:

- Python 3.10–3.13.
- Ruff, mypy, pytest, and an 80% line-coverage baseline.
- Installed wheel behavior on Windows, Ubuntu, and macOS.
- Clean-room installation and packaged MCP/Absorb resources.
- CodeQL, Dependency Review, and dependency audit reporting.

Publishing to PyPI remains a separate maintainer-authorized operation.

## 9. Extension boundaries

Prefer extensions at documented seams:

- MCP adapters and client configuration under `src/ms8/connect/`.
- Optional model providers through provider/routing interfaces.
- Policy engines through the policy engine loader contract.
- Absorb parsers through parser/registry boundaries.
- New maintenance checks through check specifications and reporter contracts.

Avoid extensions that:

- Write directly to canonical files while bypassing record policy.
- Read quarantined or source-restricted records as ordinary context.
- Introduce a second runtime-root resolver.
- Make an optional cloud or model service authoritative for local memory.
- Store secrets in repository files, logs, examples, or workflow artifacts.

## 10. Known Alpha constraints

- Internal Python modules may change before Beta.
- Data migration and compatibility policy is still being formalized.
- Some secondary stores are large and operationally complex; recovery tooling must remain explicit and tested.
- Windows support is validated through installed-wheel smoke coverage, but platform-specific service integrations may still have narrower support than core CLI operations.
- MS8 is not a security sandbox for untrusted code. It governs memory operations; it does not replace operating-system isolation.
