# MS8 0.2.18 Release Notes

MS8 0.2.18 is a patch candidate for governed Hybrid Retrieval v1 on top of the opt-in Memory Ledger v1 foundation.

## Scope

- Adds rule-first query planning with separate recorded, observed, and valid-time coordinates.
- Adds authorized lexical, optional embedding, entity, temporal, and one-to-two-hop graph candidate sources.
- Adds weighted reciprocal-rank fusion, deterministic reranking, conflict-aware MMR, compact context assembly, citations, and explain traces.
- Preserves the Ledger as the authoritative Claim, Evidence, Decision, and Conflict stream; every retrieval projection remains derived and rebuildable.
- Preserves existing legacy CLI and MCP primary response fields.
- Keeps Memory Ledger v1 and Hybrid Retrieval v1 disabled by default.
- Does not automatically migrate an existing runtime or enable the new retrieval profile.
- Does not include LAN changes, private evaluation data, private model artifacts, or automatic LTR training.
- Does not publish PyPI artifacts automatically.

## Activation boundary

Hybrid Retrieval v1 requires all Ledger-v1 authorization gates plus:

```text
memory_ledger_v1.retrieval_profile = hybrid-v1
MS8_MEMORY_HYBRID_V1=1
```

The compatibility route remains read-only. An explicitly selected but invalid Ledger/Hybrid runtime fails closed rather than falling back silently.

## Validation summary

The accepted macOS reference and Windows parity suites verify:

- Python 3.10–3.13 package boundaries;
- Ruff, mypy, full pytest, and the 80% coverage baseline;
- wheel/source build and Twine checks;
- clean-room and installation-profile checks;
- CodeQL, Dependency Review, and Python dependency audit;
- identical frozen ordered claim identifiers and full retrieval-trace fingerprints on macOS and Windows;
- Unicode and space-containing Windows paths;
- SQLite quick-check, atomic replacement, cross-process locking, overlapping rebuild safety, and interrupted-write preservation;
- safe degradation when the optional embedding provider is unavailable;
- installed `ms8`, `ms8-recovery`, and `ms8-memory-ledger` entry points.

On the six-case public synthetic acceptance fixture, Hybrid Retrieval v1 improved nDCG@10 by approximately 21.03% relative to the legacy baseline, improved Recall@20 from `0.9166666666666666` to `1.0`, kept unauthorized/inactive error recall at `0.0`, and achieved complete Evidence citation and degradation-correctness gates.

This synthetic fixture validates contracts and critical safety slices. It is not a production-scale corpus or a default-enable performance benchmark. The measured P95 includes cold-start cost and is not a latency SLO.

## Compatibility

- Existing installations continue to use their current runtime and retrieval path unless explicitly migrated and configured.
- Existing MCP query/context/prepare-reply required fields remain available.
- Hybrid-specific diagnostics are additive under Ledger-v1 trace payloads.
- Ollama remains optional under `ms8[llm]` or `ms8[full]` and is not an unconditional core dependency.
- No HNSW, cross-encoder, or LTR package is required by the core installation.

## Release candidate checklist

- [x] macOS reference acceptance completed.
- [x] Windows frozen-contract parity completed.
- [x] Hybrid feature remains opt-in and disabled by default.
- [x] Legacy CLI/MCP primary fields remain compatible.
- [x] No automatic migration, tag, Release, or PyPI publication was performed by the development PR.
- [ ] Complete final documentation and artifact-boundary convergence on the exact PR head.
- [ ] Generate authoritative release-candidate evidence from the exact post-merge commit intended for the tag.
- [ ] Create the final `v0.2.18` tag and GitHub Release only after explicit maintainer approval.
- [ ] Publish the already-verified artifacts to PyPI only after explicit maintainer approval.

See [Hybrid Retrieval v1](HYBRID_RETRIEVAL_V1.md) for the implementation, security, CLI/MCP, and evaluation boundaries.
