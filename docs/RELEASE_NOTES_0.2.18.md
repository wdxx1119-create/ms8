# MS8 0.2.18 Release Notes

MS8 0.2.18 is a patch release that adds the opt-in Hybrid Retrieval v1 profile for Memory Ledger v1.

## User-visible changes

- Adds an explicitly selected Hybrid Retrieval v1 profile for Ledger-v1 query and context operations.
- Supports independent recorded, observed, and valid-time query coordinates.
- Preserves Claim, Evidence, Decision, and Conflict state as authoritative Ledger data.
- Preserves existing legacy CLI and MCP primary response fields.
- Adds optional explain diagnostics for authorized retrieval operations.
- Keeps Memory Ledger v1 and Hybrid Retrieval v1 disabled by default.
- Does not automatically migrate an existing runtime or enable the new profile.
- Does not include LAN behavior changes.
- Does not make optional local-model dependencies mandatory for the core installation.

## Activation

Hybrid Retrieval v1 requires the normal Ledger-v1 configuration and runtime manifest plus:

```text
memory_ledger_v1.retrieval_profile = hybrid-v1
memory_ledger_v1.hybrid.principal_realm_ids = [project:ms8]
memory_ledger_v1.hybrid.principal_scopes = [project]
MS8_MEMORY_HYBRID_V1=1
```

The process must also receive `MS8_MEMORY_LEDGER_V1=1`.

An explicitly selected but invalid Ledger/Hybrid runtime fails closed rather than silently falling back to an unrestricted read.

## Compatibility

- Existing installations continue to use their current runtime and retrieval path unless explicitly configured otherwise.
- Existing MCP `query`, `context`, and `prepare_reply` required fields remain available.
- Hybrid-specific options and diagnostics are additive.
- The Ledger-v1 compatibility route remains read-only.
- Ollama remains optional under `ms8[llm]` or `ms8[full]`.
- No HNSW, cross-encoder, or learning-to-rank package is required by the core installation.

## Security boundary

The release preserves the existing authorization model:

- realm, scope, sensitivity, lifecycle, governance, and time restrictions are applied before results are returned;
- revoked, forgotten, quarantined, and expired-current claims cannot re-enter through optional retrieval components;
- final injectable facts retain accessible Evidence and Decision traces;
- optional-provider failure degrades safely without widening authorization;
- full explain traces should be treated as sensitive diagnostic output.

## Validation summary

The release was exercised on Python 3.10–3.13 and validated on macOS and Windows. Validation covers static checks, full tests and coverage, package/profile installation, clean wheel and source-distribution installation, dependency auditing, CodeQL, authorization isolation, current and historical queries, conflict presentation, optional-provider degradation, Unicode and space-containing Windows paths, SQLite safety, and deterministic cross-platform result ordering.

The public reference fixtures are synthetic contract tests. They are not a production-scale corpus benchmark or a latency service-level objective.

## Known limitations

- Hybrid Retrieval v1 remains experimental and disabled by default.
- Automatic runtime migration is not included.
- Linux remains best-effort rather than a formal reference platform for this release.
- Optional local-model behavior depends on the installed provider and model.
- Default enablement requires a separate maintainer decision.

See [Hybrid Retrieval v1](HYBRID_RETRIEVAL_V1.md) for activation, CLI/MCP usage, security properties, and operational limitations.

MS8 0.2.18 was published as tag `v0.2.18`, a GitHub Release, and a PyPI release. This release does not automatically enable Hybrid Retrieval v1.
