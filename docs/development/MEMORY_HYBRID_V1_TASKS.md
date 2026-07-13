# Memory Hybrid Retrieval v1 — staged task plan

Branch: `feature/memory-hybrid-v1-macos-first`  
Base: `main@df9187d28f20f18872b1fa642382a314edadef6a`  
Target patch: `0.2.18` candidate scope  
Delivery order: macOS reference implementation first, Windows adaptation second.

## Scope and non-negotiable boundaries

Hybrid Retrieval v1 upgrades Ledger v1 retrieval from a single deterministic lexical path into a governed, explainable, multi-source retrieval pipeline.

The following invariants are release gates rather than ranking preferences:

- Policy and eligibility filtering run before every retriever.
- A retriever only sees claim identifiers already authorized for the current principal, realm, scope, lifecycle, sensitivity, purpose, and time coordinates.
- Revoked, forgotten, quarantined, expired-current, or otherwise ineligible claims cannot enter an injection candidate set.
- Learning-to-rank, embeddings, graph traversal, and cross-encoders cannot modify policy outcomes, lifecycle state, authority, or action permissions.
- Final Agent context is claim-based and must include accessible Evidence and Decision traces.
- Projection or model failure degrades to an authorized deterministic lexical path, never to an unfiltered search.
- Existing Ledger v1 remains opt-in and disabled by default.
- Existing legacy CLI/MCP behavior remains compatible until explicit migration gates are passed.
- No user data, private evaluation corpus, private repository references, local absolute paths, credentials, or commercial policy implementation may be committed.

## Delivery model

### Stable patch deliverable

`hybrid-v1`:

- governed query planning;
- lexical, vector, entity, temporal, and graph candidate sources;
- weighted reciprocal-rank fusion;
- deterministic rule reranking;
- conflict-aware MMR;
- compact context assembly with citations and policy boundary;
- explain trace and locked evaluation suite;
- safe component-level degradation.

### Deferred enhancement

`ltr-v1`:

- feature registry and model interface may be prepared in this branch;
- model training, private/local labels, LambdaMART artifacts, online feedback, and optional cross-encoder promotion are not required for the stable `hybrid-v1` patch;
- LTR must remain optional, verifiable, locally loadable, and automatically reversible to deterministic hybrid ranking.

## Status legend

- `[ ]` not started
- `[-]` in progress
- `[x]` completed and locally/CI validated
- `[!]` blocked or requires maintainer decision

---

## Phase 0 — branch safety and baseline

- [x] Create public development branch from the latest `main` commit.
- [x] Record the implementation plan and public/private boundary in-repository.
- [x] Open a Draft PR marked experimental, disabled by default, macOS-first, no release/publish action.
- [x] Capture baseline CI, unit-test, package-smoke, and current Ledger retrieval behavior.
- [x] Add a branch safety check covering private repository names, real local paths, credentials, private fixtures, and LAN files.

Acceptance:

- the branch contains only public-safe material;
- no default behavior or runtime format changes;
- baseline behavior is reproducible before implementation changes.

## Phase 1 — retrieval contracts and eligibility boundary

- [x] Add the `ms8.memory.retrieval` package boundary.
- [x] Define immutable contracts:
  - `Principal`;
  - `MemoryQuery`;
  - `RetrievalPlan`;
  - `CandidateHit`;
  - `RankedClaim`;
  - `RetrievalTrace`;
  - time-coordinate and candidate-limit value objects.
- [x] Preserve separate `recorded_as_of`, `observed_as_of`, and `valid_at` fields; treat `as_of` only as a convenience expansion.
- [x] Define normalized retrieval purposes such as `recall`, `prepare_reply`, `inject`, `historical`, `review`, and `audit`.
- [x] Add authority normalization without rewriting historical Ledger data (`assistant_inferred` compatibility alias to `agent_inferred`).
- [x] Implement `EligibilityEvaluator` as the single pre-retrieval boundary.
- [x] Produce an immutable eligible-claim set and structured blocked-reason counts.
- [x] Ensure all candidate sources require the eligibility set rather than accepting an unrestricted store.
- [x] Add tests proving unauthorized realms, inactive lifecycles, non-injectable claims, and missing principal context fail closed.

Acceptance:

- candidate-source APIs cannot be called without an eligibility boundary;
- policy results are deterministic and explainable;
- no ranker can add an ineligible claim.

## Phase 2 — query planner and analyzers

- [x] Implement rule-first `QueryPlanner` with optional classifier extension points.
- [x] Support intents:
  - `current_state`;
  - `historical_reason`;
  - `project_rule`;
  - `personal_preference`;
  - `code_symbol`;
  - `open_recall`.
- [x] Implement explicit temporal parsing for relative and absolute expressions.
- [x] Implement unified Chinese, English, and code token analysis:
  - Jieba segmentation with CJK unigram/bigram fallback;
  - English case folding and conservative variants;
  - exact code/path/version/flag preservation;
  - identifier expansion for camelCase, PascalCase, snake_case, and kebab-case.
- [x] Add tests for commands, paths, versions, function calls, `C++`, and mixed Chinese/English/code queries.

Acceptance:

- query plans are serializable and explainable;
- temporal expressions resolve to explicit coordinates or intervals;
- analyzers do not destroy exact project tokens.

## Phase 3 — candidate source adapters

- [x] Define a common `CandidateSource` protocol returning `CandidateHit` only.
- [x] Wrap current Ledger Search/FTS projection as the lexical source.
- [x] Preserve legacy `engine_core/whoosh_search.py` as a compatibility adapter rather than a new authority path.
- [x] Preserve legacy `engine_core/semantic_search.py` as a candidate adapter only.
- [x] Preserve legacy knowledge graph as a candidate adapter only.
- [x] Ensure every hit maps to a Ledger `claim_id` and accessible `evidence_ids`.
- [x] Add component health and structured degradation reasons.

Acceptance:

- no source returns raw files or conversation chunks as final results;
- each source has deterministic limits and trace output;
- source failure does not bypass eligibility.

Validation evidence: exact Phase 3 head `18c0146d8519b7835b133bca23710c1acc853198` passed CI, Required check compatibility, CodeQL, Dependency Review, and Examples smoke.

## Phase 4 — lexical and embedding projections

- [x] Upgrade claim/evidence lexical indexing fields:
  - claim text;
  - subject/predicate/value;
  - aliases;
  - code symbols, paths, versions, commands;
  - compact evidence text;
  - realm, scope, lifecycle, and valid-time metadata.
- [x] Keep deterministic `vector_projection.v1` compatibility unchanged.
- [x] Add a separate versioned `embedding_projection` contract using `content_hash + model_id`.
- [x] Define `EmbeddingProvider` protocol independent of Ollama.
- [x] Provide optional Ollama adapter without making Ollama a core dependency.
- [x] Implement small-dataset exact cosine search after eligibility restriction.
- [x] Reserve an optional HNSW backend interface without requiring it in the core package.
- [x] Rebuild embedding projection on model/content-version mismatch.

Acceptance:

- an embedding backend never scans unauthorized candidates;
- missing embeddings degrade to lexical retrieval;
- model changes are visible in projection state and explain trace.

Validation evidence: exact Phase 4 head `f525f3a0c28fef4ab0ec6978b952c6394e261374` passed Python 3.10–3.13, Ruff, mypy, full pytest and coverage, package/profile/clean-room checks, macOS and Windows wheel smoke, CodeQL, Dependency Review, Examples smoke, and Required check compatibility.

## Phase 5 — entity, temporal, and graph retrieval

- [x] Build deterministic entity aliases from structured claims and evidence.
- [x] Implement entity exact/alias matching without requiring an LLM.
- [x] Implement current-state temporal retrieval.
- [x] Implement historical retrieval over superseded/expired claims only when explicitly requested and still recall-authorized.
- [x] Keep unknown-basis time records supplementary to explicit-time facts.
- [x] Implement one-to-two-hop local graph expansion.
- [x] Restrict graph traversal by eligibility, realm, lifecycle, and evidence-backed edges.
- [x] Return explainable graph paths.

Acceptance:

- current questions do not return obsolete rules as current facts;
- historical questions can retrieve old decisions with evidence;
- graph traversal cannot cross a policy boundary.

Validation evidence: exact Phase 5 head `9b5eb9688676db0888cdb57a8bf6bfe974d3797c` passed Python 3.10–3.13, Ruff, mypy, full pytest and coverage, package/profile/clean-room checks, macOS and Windows wheel smoke, CodeQL, Dependency Review, Examples smoke, and Required check compatibility.

## Phase 6 — fusion and deterministic reranking

- [x] Implement weighted Reciprocal Rank Fusion with versioned configuration.
- [x] Deduplicate by claim identifier before ranking.
- [x] Add deterministic signals:
  - fused retrieval score;
  - authority;
  - evidence strength using independent source keys;
  - temporal currentness;
  - scope/intent match;
  - status/verification;
  - conflict handling;
  - type-aware freshness.
- [x] Keep hard rules outside score competition.
- [x] Prevent agent inference from outranking explicit user or verified project facts for the same predicate.
- [x] Add stable tie-breaking and full ranking explanation.

Acceptance:

- identical inputs and projections produce identical order on macOS and Windows;
- no weighted score can overcome an eligibility or authority hard rule;
- duplicated chunks from one source do not inflate evidence strength.

Validation evidence: exact Phase 6 head `d46dc72e22c103c063cba835725b32821ff2242d` passed Python 3.10–3.13, Ruff, mypy, full pytest and coverage, package/profile/clean-room checks, macOS and Windows wheel smoke, CodeQL, Dependency Review, Examples smoke, and Required check compatibility.

## Phase 7 — MMR and context assembly

- [x] Implement MMR with dense similarity and token/Jaccard fallback.
- [x] Apply claim-level deduplication.
- [x] Enforce subject/predicate diversity limits.
- [x] Preserve unresolved conflict candidates and warnings.
- [x] Reserve context budget for citations and boundary metadata.
- [x] Emit compact claim facts rather than raw document dumps.
- [x] Require at least one accessible Evidence and Decision trace for injection.
- [x] Add explicit policy-boundary text to Agent context.

Acceptance:

- selected context is within token budget;
- every injected fact is traceable;
- unresolved conflicts are not silently removed by diversity logic.

Validation evidence: exact Phase 7 head `2046cfaa8381b9287f144d7870cb379be457c4cf` passed Python 3.10–3.13, Ruff, mypy, full pytest and coverage, package/profile/clean-room checks, macOS and Windows wheel smoke, CodeQL, Dependency Review, Examples smoke, and Required check compatibility.

## Phase 8 — integration and explain surfaces

- [ ] Integrate hybrid retrieval behind an explicit Ledger v1 feature/profile gate.
- [ ] Connect CLI query/context/explain routes.
- [ ] Connect MCP query/context/prepare-reply routes while preserving response compatibility.
- [ ] Add `--explain` output with plan, eligibility, source hits, fusion, reranking, MMR, assembly, and degradation reasons.
- [ ] Preserve fail-closed behavior when Ledger v1 is explicitly selected but invalid.
- [ ] Preserve legacy behavior when Ledger v1/hybrid mode is not selected.

Acceptance:

- no automatic runtime-format migration;
- no automatic feature enablement;
- old clients retain their required primary fields.

## Phase 9 — evaluation and macOS reference acceptance

- [ ] Add public, synthetic, reproducible evaluation fixtures.
- [ ] Add metrics:
  - nDCG@5 and nDCG@10;
  - MRR;
  - Recall@20;
  - current/historical fact accuracy;
  - evidence citation coverage;
  - conflict presentation rate;
  - unauthorized/inactive error-recall rate;
  - language/code slices;
  - P50/P95 latency;
  - degradation correctness.
- [ ] Add baseline comparison: legacy versus `hybrid-v1`.
- [ ] Add macOS acceptance script and artifact report.
- [ ] Freeze public contracts and golden ordering after macOS acceptance.

Release gates for macOS reference implementation:

- unauthorized, revoked, forgotten, wrong-realm, and expired-current error recall is `0`;
- all final injectable results have Evidence and Decision traces;
- any dense/entity/graph source may fail while safe lexical retrieval remains available;
- locked-set nDCG@10 improves by at least 5% relative to legacy without Recall@20 regression;
- no critical current, historical, conflict, or code-symbol regression.

## Phase 10 — Windows adaptation and parity

- [ ] Port without forking ranking semantics.
- [ ] Validate Unicode and space-containing paths.
- [ ] Validate SQLite and projection handle release before atomic replacement.
- [ ] Validate file locks, concurrent projection rebuild, and interrupted writes.
- [ ] Validate optional embedding subprocess/provider behavior.
- [ ] Add clean-wheel Windows smoke and installed-entry-point tests.
- [ ] Compare macOS/Windows plans, eligibility sets, scores, tie-breaks, selected claims, and traces on frozen fixtures.

Acceptance:

- platform-specific code is limited to IO/process/provider boundaries;
- frozen ranking fixtures produce equivalent ordered claim identifiers;
- all Windows release-boundary checks pass.

## Phase 11 — patch convergence

- [ ] Run Ruff, mypy, full pytest, coverage, package build, Twine check, dependency audit, CodeQL, and clean-room profiles.
- [ ] Verify no LAN files or private assets enter artifacts.
- [ ] Verify default install remains free of optional embedding/LTR dependencies.
- [ ] Update architecture, security, data-model, CLI/MCP, and release documentation.
- [ ] Generate exact-commit candidate evidence after final merge preparation.
- [ ] Keep PR as Draft until macOS and Windows acceptance reports are complete.
- [ ] Do not tag, publish PyPI, or create a final Release without explicit maintainer approval.

## LTR v1 preparation — non-blocking

- [ ] Define a versioned feature schema and transform interface.
- [ ] Define a ranker protocol and deterministic fallback contract.
- [ ] Define signed/checksummed local model package metadata.
- [ ] Define train/validate/promote CLI contracts without enabling automatic training.
- [ ] Keep private/local labels and trained artifacts outside the public repository.
- [ ] Require a statistically significant locked-test improvement before promotion.

## Progress reporting rule

After each phase:

1. run the phase-specific tests and relevant full-suite gates;
2. inspect the diff for architecture drift, compatibility risk, security regressions, private-data leakage, and platform assumptions;
3. update this checklist with completed items and evidence references;
4. post a progress report before moving into the next major phase.
