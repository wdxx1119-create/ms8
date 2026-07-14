# Hybrid Retrieval v1

Hybrid Retrieval v1 is an experimental, read-only retrieval profile for Memory Ledger v1. It is opt-in and disabled by default.

This document covers the public operating contract: activation, compatibility, security boundaries, supported interfaces, degradation behavior, and current limitations. It does not enable the feature automatically or migrate existing installations.

## Public behavior

When explicitly enabled, Hybrid Retrieval v1 can improve Ledger-v1 query and context operations by combining authorized retrieval signals while preserving the Ledger as the source of truth.

The following guarantees remain unchanged:

- Ledger transactions and replayed Claim, Evidence, Decision, and Conflict state remain authoritative;
- retrieval projections are derived and rebuildable;
- policy and eligibility checks run before candidates can be returned;
- retrieval scores cannot grant recall, injection, or action permission;
- existing legacy CLI and MCP primary response fields remain compatible;
- existing installations continue to use their current retrieval path unless explicitly configured otherwise.

## Activation requirements

Hybrid Retrieval v1 is constructed only when all required gates are present:

1. `memory_ledger_v1.enabled` is `true`;
2. the runtime-format manifest selects `ledger-v1`;
3. `MS8_MEMORY_LEDGER_V1` is enabled in the process environment;
4. `memory_ledger_v1.retrieval_profile` is `hybrid-v1`;
5. `MS8_MEMORY_HYBRID_V1` is enabled in the process environment;
6. the Ledger verifies successfully and required projections are ready for the current Ledger head.

An explicitly selected but invalid Ledger-v1 or Hybrid-v1 route fails closed. It does not silently fall back to an unrestricted or legacy read.

## CLI usage

```bash
export MS8_MEMORY_LEDGER_V1=1
export MS8_MEMORY_HYBRID_V1=1

ms8 memory-ledger \
  --workspace /path/to/ms8-workspace \
  --retrieval-profile hybrid-v1 \
  --principal-realm-id project:ms8 \
  --principal-scope project \
  query "release rule" \
  --realm-id project:ms8 \
  --scope project \
  --explain
```

Context assembly uses the same explicit profile:

```bash
ms8 memory-ledger \
  --workspace /path/to/ms8-workspace \
  --retrieval-profile hybrid-v1 \
  --principal-realm-id project:ms8 \
  --principal-scope project \
  context "prepare the release summary" \
  --realm-id project:ms8 \
  --scope project \
  --explain
```

The workspace must already contain an authorized Ledger-v1 runtime manifest, a valid Ledger, and fresh required projections. These commands do not migrate or enable the runtime automatically.

Supported read options include realm, scope, purpose, result limit, explain output, and independent `recorded_as_of`, `observed_as_of`, and `valid_at` time coordinates.

## MCP usage

A representative configuration is:

```yaml
memory_core:
  workspace: /path/to/ms8-workspace
memory_ledger_v1:
  enabled: true
  retrieval_profile: hybrid-v1
  context_token_budget: 1200
  hybrid:
    principal_realm_ids: [project:ms8]
    principal_scopes: [project]
```

The MCP server process must also receive:

```text
MS8_MEMORY_LEDGER_V1=1
MS8_MEMORY_HYBRID_V1=1
```

The existing `query`, `context`, and `prepare_reply` surfaces preserve their required primary fields. Hybrid-specific options and diagnostics are additive.

`principal_realm_ids` and `principal_scopes` are required for Hybrid Retrieval. The CLI supplies the same boundary with `--principal-realm-id` and `--principal-scope`; `--realm-id` and `--scope` remain request filters. Requests outside the configured principal boundary fail closed.

When Ledger v1 is explicitly selected, writes remain disabled through the compatibility read adapter. A connected client cannot use this interface to bypass the governed write path.

## Security boundary

Hybrid Retrieval v1 enforces the following public safety contract:

- unauthorized realms and scopes are excluded before retrieval;
- blocked sensitivities and inactive lifecycle states are excluded;
- revoked, forgotten, quarantined, and expired-current claims cannot re-enter through an optional retrieval component;
- historical claims are not presented as current facts unless the request explicitly asks for historical reasoning and the claims remain recall-authorized;
- final injectable facts retain accessible Evidence and Decision traces;
- unresolved conflicts remain visible rather than being silently collapsed;
- optional-provider failure is reported as degradation and does not widen authorization;
- full explain traces may contain sensitive identifiers and should be sanitized before public issue reports.

## Optional dependencies

The core installation does not require Ollama, HNSW, a cross-encoder, or an LTR package.

Ollama support remains optional under:

```bash
pip install "ms8[llm]"
```

When an optional provider is unavailable or invalid, the request degrades to an authorized deterministic retrieval path. It does not scan an unrestricted claim store.

## Compatibility and validation

The `0.2.18` candidate was validated on macOS and Windows with the same public retrieval contracts. Validation covers authorization isolation, current and historical queries, conflict presentation, code-symbol queries, optional-provider degradation, package installation, Unicode and space-containing paths, and deterministic cross-platform result ordering.

The reference fixtures are synthetic contract tests. They do not constitute a production-scale corpus benchmark or a latency service-level objective.

## Current limitations

- the feature is disabled by default;
- automatic runtime migration is not provided;
- Linux remains best-effort rather than a formal reference platform for this candidate;
- explain output is diagnostic and may expose sensitive metadata;
- optional local-model behavior depends on the installed provider and model;
- default enablement requires a separate release decision.

## Release boundary

The public candidate must continue to satisfy:

- clean wheel and source-distribution metadata;
- no private fixtures, credentials, local user data, or LAN package material in release artifacts;
- no unconditional embedding or learning-to-rank dependency in the core installation;
- clean installation and `pip check` validation;
- exact-commit macOS and Windows acceptance evidence.

No tag, GitHub Release, PyPI publication, or automatic default enablement is authorized by this document.
