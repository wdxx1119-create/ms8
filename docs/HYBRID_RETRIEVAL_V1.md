# Hybrid Retrieval v1

Hybrid Retrieval v1 is the governed multi-source retrieval layer built on top of Memory Ledger v1. It is prepared for the `0.2.18` patch candidate, but remains experimental, read-only, opt-in, and disabled by default.

This document describes the actual implementation boundary. It is not a commitment to enable Hybrid Retrieval v1 automatically or to replace the legacy retrieval path in existing installations.

## Expected release state

The intended stable patch outcome is:

- Memory Ledger v1 remains the authoritative append-only record and decision stream;
- Hybrid Retrieval v1 can be selected explicitly for Ledger-v1 query and context operations;
- existing legacy CLI and MCP primary response fields remain compatible;
- ranking, embeddings, entity matching, temporal retrieval, and graph traversal cannot change policy or lifecycle decisions;
- macOS and Windows use the same retrieval implementation and frozen ranking semantics;
- no automatic runtime migration, feature enablement, tag, Release, or PyPI publication occurs from the development PR.

The patch is not intended to make Hybrid Retrieval v1 the default retrieval engine. Default enablement requires broader real-corpus evaluation, explicit latency budgets, upgrade guidance, and a separate maintainer decision.

## Activation gates

Hybrid Retrieval v1 is constructed only when every required gate is present:

1. `memory_ledger_v1.enabled` is `true` in the supplied configuration;
2. the authoritative runtime-format manifest selects `ledger-v1`;
3. `MS8_MEMORY_LEDGER_V1` is enabled in the process environment;
4. `memory_ledger_v1.retrieval_profile` is `hybrid-v1`;
5. `MS8_MEMORY_HYBRID_V1` is enabled in the process environment;
6. the Ledger verifies successfully and all required projections are ready for the same Ledger head.

An explicitly selected Ledger-v1 or Hybrid-v1 route fails closed. It does not silently fall back to a legacy read when the Ledger, manifest, configuration, or projection state is invalid.

## Retrieval architecture

The implemented pipeline is:

```text
query
  -> rule-first query planning and time-coordinate resolution
  -> principal / realm / scope / sensitivity / lifecycle eligibility
  -> authorized candidate sources only
       - lexical Search projection
       - optional embedding projection
       - deterministic entity aliases
       - current or explicitly requested historical temporal source
       - one-to-two-hop governed graph expansion
  -> weighted reciprocal-rank fusion
  -> deterministic rule reranking
  -> conflict-aware MMR and diversity limits
  -> compact claim context with Evidence, Decision and policy traces
```

Policy filtering precedes every candidate source. Candidate sources receive an immutable eligible-claim set and cannot widen it. Fusion, deterministic reranking, MMR, embeddings, and graph traversal operate only on already-authorized claim identifiers.

## Authority and data model

The authority hierarchy is unchanged:

1. Ledger transactions and replayed Claim, Evidence, Decision, and Conflict state are authoritative.
2. SQLite, Search, FTS, Vector, Graph, and Embedding projections are derived and rebuildable.
3. Retrieval traces and evaluation reports are diagnostic evidence, not authority.

Hybrid Retrieval v1 preserves separate query time coordinates:

- `recorded_as_of`;
- `observed_as_of`;
- `valid_at`.

Historical claims are not treated as current facts. Superseded or expired claims are considered only when the query purpose or intent explicitly requests historical reasoning and the claims remain recall-authorized.

Every final injectable claim must retain accessible Evidence and Decision traces. A retrieval score cannot grant recall, injection, or action permission.

## Optional embeddings

The core installation does not require Ollama, HNSW, a cross-encoder, or an LTR library.

- `EmbeddingProvider` is a provider-independent protocol.
- The Ollama adapter is optional and remains under the existing `llm` or `full` installation profiles.
- Exact cosine search is used after eligibility restriction for the current small-dataset path.
- Missing or invalid embeddings degrade to authorized deterministic lexical retrieval.
- Model and content identity are visible through `content_hash + model_id` projection metadata.

No embedding backend may scan an unrestricted claim store.

## CLI surface

The CLI route remains explicit:

```bash
export MS8_MEMORY_LEDGER_V1=1
export MS8_MEMORY_HYBRID_V1=1

ms8 memory-ledger \
  --workspace /path/to/ms8-workspace \
  --retrieval-profile hybrid-v1 \
  query "old release rule" \
  --purpose historical \
  --explain
```

Context assembly uses the same explicit profile:

```bash
ms8 memory-ledger \
  --workspace /path/to/ms8-workspace \
  --retrieval-profile hybrid-v1 \
  context "prepare the release summary" \
  --explain
```

The workspace must already contain an authorized Ledger-v1 runtime manifest, a valid Ledger, and fresh required projections. The CLI does not migrate or enable the runtime automatically.

Supported read options include the independent time coordinates, `realm_id`, `scope`, retrieval purpose, result limit, and explain output. The explain trace covers planning, eligibility, source health, source hits, fusion, deterministic reranking, MMR, assembly, policy boundaries, and degradation reasons.

## MCP surface

A representative MCP-side configuration is:

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

The MCP server process must also receive both environment gates:

```text
MS8_MEMORY_LEDGER_V1=1
MS8_MEMORY_HYBRID_V1=1
```

The existing `query`, `context`, and `prepare_reply` surfaces preserve their required primary fields. Hybrid additions are additive:

- `query` supports `purpose`, `explain`, independent time coordinates, realm, and scope;
- `context` and `prepare_reply` support `explain`, independent time coordinates, realm, and scope;
- `retrieval_gateway` identifies the Ledger head, profile, manifest generation, migration identity, and policy trace;
- detailed Hybrid traces are returned under the Ledger-v1 diagnostic payload.

When Ledger v1 is explicitly selected, MCP writes remain disabled through this compatibility adapter. A connected client cannot use the read adapter to bypass the governed write path.

## Security properties

Hybrid Retrieval v1 enforces the following non-negotiable rules:

- unauthorized realms, disallowed scopes, blocked sensitivities, inactive lifecycle states, or non-recallable claims are removed before retrieval;
- revoked, forgotten, quarantined, and expired-current claims cannot re-enter through vector, entity, temporal, or graph sources;
- graph traversal removes unauthorized claim nodes and related edges before traversal;
- unresolved conflicts remain visible instead of being silently removed by diversity logic;
- agent-inferred claims cannot outrank explicit user or verified project facts for the same predicate through score competition;
- context assembly reserves budget for citations and emits an explicit policy-boundary marker;
- optional-provider failure is reported as degradation rather than authorization fallback.

Explain traces can expose claim identifiers, realm/scope metadata, Evidence identifiers, Decision identifiers, conflict structure, and provider health. Treat full traces as sensitive diagnostic output and sanitize them before public issue reports.

## Validation result and its limits

The frozen public acceptance set contains six synthetic cases covering current facts, historical facts, Chinese preference retrieval, code-symbol retrieval, unresolved conflicts, and a wrong-realm probe.

On the exact accepted reference head:

- Hybrid nDCG@10: `0.8333333333333334`;
- legacy nDCG@10: `0.6885076050645943`;
- relative nDCG@10 improvement: approximately `21.03%`;
- Hybrid Recall@20: `1.0`;
- legacy Recall@20: `0.9166666666666666`;
- unauthorized/inactive error-recall rate: `0.0`;
- Evidence citation coverage: `1.0`;
- historical fact accuracy: `1.0`;
- degradation correctness: `1.0`;
- macOS and Windows frozen ordered claim identifiers and full-trace fingerprints match.

The median synthetic-case latency is low after initialization, but the measured P95 includes a cold-start outlier and is not a release latency SLO. The six-case public fixture proves deterministic contracts and critical safety slices; it is not a representative production corpus, a scale benchmark, or sufficient evidence for default enablement.

## Release-boundary checks

The candidate artifact gate verifies:

- wheel and source distribution metadata pass Twine validation;
- no Hybrid patch artifact path contains the excluded LAN package or private fixture/credential material;
- optional embedding and LTR dependencies are not unconditional core requirements;
- the core wheel installs in a clean environment and passes `pip check`;
- macOS and Windows reference/parity reports are generated from the exact tested commit.

The final authoritative candidate evidence must still be generated from the exact post-merge commit intended for the release tag.

## Deferred work

The following items are deliberately outside the stable Hybrid-v1 patch:

- automatic default enablement;
- online learning or automatic model training;
- private/local labels in the public repository;
- promoted LambdaMART or other LTR model artifacts;
- cross-encoder promotion;
- automatic runtime migration;
- LAN integration changes.

LTR v1 preparation remains non-blocking and must preserve a deterministic Hybrid-v1 fallback.
