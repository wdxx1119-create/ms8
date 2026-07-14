# Memory Hybrid Retrieval v1 — target-to-actual gap review

Review scope: Draft PR #39, branch `feature/memory-hybrid-v1-macos-first`.

This review separates three different meanings of "complete":

1. **implementation complete** — the planned Hybrid Retrieval v1 code path exists;
2. **patch-candidate complete** — the code, documentation, package boundary, and exact-head CI evidence are converged for a `0.2.18` candidate;
3. **default-production complete** — the feature is suitable to replace the existing retrieval path automatically for ordinary users.

Only the first two are goals of this patch. Default-production enablement is explicitly outside the PR boundary.

## Intended patch outcome

The actual target is an opt-in, read-only, governed Hybrid Retrieval v1 profile on top of Memory Ledger v1:

- Ledger transactions and replayed Claim, Evidence, Decision, and Conflict state remain authoritative;
- policy and eligibility filtering precede every candidate source;
- lexical, optional embedding, entity, temporal, and graph sources operate only on authorized claim identifiers;
- fusion, deterministic reranking, and MMR cannot change policy, lifecycle, realm, sensitivity, authority, or action permissions;
- final injectable context remains claim-based and Evidence/Decision traceable;
- existing legacy CLI and MCP primary response fields remain compatible;
- the profile remains disabled by default and requires explicit Ledger-v1 and Hybrid-v1 gates;
- no automatic migration, tag, GitHub Release, or PyPI publication occurs from the development PR.

The target is **not** a new default retrieval engine, hosted service, automatic learner, or task orchestrator.

## Target versus actual implementation

| Area | Target | Actual state | Gap |
|---|---|---|---|
| Eligibility boundary | Every source sees only an immutable authorized claim set | Implemented and covered by denied realm, scope, sensitivity, lifecycle, governance, time, Evidence, Decision, and principal tests | No known target gap |
| Query planning | Rule-first multilingual and code-aware plans with separate time coordinates | Implemented for current, historical, project-rule, preference, code-symbol, and open-recall intents | Optional classifier remains an extension point, as planned |
| Candidate sources | Lexical, vector, entity, temporal, and graph sources | Implemented; embedding is optional; graph is one-to-two-hop and prefiltered | No HNSW backend is promoted; exact cosine is the stable small-dataset path |
| Fusion/reranking | Versioned weighted RRF plus deterministic authority/evidence/time/conflict rules | Implemented with stable tie-breaking and explanations | No trained LTR model, intentionally deferred |
| Context assembly | Budgeted, cited, conflict-aware, policy-bounded claim context | Implemented with Evidence/Decision traces and explicit policy-boundary text | No known target gap |
| CLI integration | Explicit Ledger-v1 Hybrid profile, purpose/time/scope and explain inputs | Implemented behind explicit profile and environment gates | Not exposed through the ordinary legacy `ms8 ask` path, by design |
| MCP integration | Preserve primary fields while adding Hybrid options and traces | Implemented for query, context, and prepare-reply; compatibility adapter remains read-only | Normal MCP bootstrap does not enable Hybrid automatically, by design |
| macOS reference | Frozen public acceptance and report | Passed | Public fixture is synthetic and small |
| Windows parity | Same ranking implementation and frozen semantic output | Passed exact ordered-claim and full-trace fingerprints, installed wheel, Unicode/space paths, SQLite, locks, replacement, interruption, and degradation gates | No Windows semantic fork remains in the target scope |
| Packaging | Core install free of mandatory embedding/LTR stack; no LAN/private material | Exact wheel/sdist boundary report passes | Final authoritative post-merge candidate bundle is still required |
| Version convergence | Candidate identity is `0.2.18` | `pyproject.toml`, source fallback, Changelog, and release notes are `0.2.18` | Root README still contains `0.2.17` badge/example text and must be aligned before final candidate evidence |
| Publication | No automatic publish | No tag, Release, merge, default enablement, or PyPI action performed | Maintainer approval remains mandatory |

## Evidence-backed functional result

The accepted public fixture contains six synthetic cases covering:

- current release rules;
- historical release rules;
- Chinese preference retrieval;
- code-symbol retrieval;
- unresolved conflict presentation;
- a wrong-realm authorization probe.

Observed acceptance results:

- Hybrid nDCG@10: `0.8333333333333334`;
- legacy nDCG@10: `0.6885076050645943`;
- relative nDCG@10 improvement: approximately `21.03%`;
- Hybrid Recall@20: `1.0`;
- legacy Recall@20: `0.9166666666666666`;
- unauthorized/inactive error-recall rate: `0.0`;
- Evidence citation coverage: `1.0`;
- historical fact accuracy: `1.0`;
- degradation correctness: `1.0`;
- macOS and Windows frozen ordered claim identifiers and complete retrieval-trace fingerprints match.

These results are strong evidence for deterministic contracts, policy isolation, critical retrieval slices, and cross-platform semantic parity. They are not evidence of production-scale retrieval quality across a large heterogeneous personal corpus.

## Remaining patch-candidate work

The remaining work is convergence rather than missing core retrieval functionality:

1. align the root README badge, wheel example, and validation-matrix version with `0.2.18`;
2. complete exact-head CI after the final documentation/package-boundary commits;
3. inspect and retain the exact-head Hybrid artifact-boundary report;
4. update the staged task checklist and PR progress evidence from that exact head;
5. after review and merge preparation, generate authoritative release-candidate evidence from the exact post-merge commit intended for the tag;
6. keep tag, GitHub Release, and PyPI publication behind explicit maintainer approval.

## Deliberate non-goals and residual validation gaps

The following are not patch blockers because they were never part of stable Hybrid-v1 scope, but they matter before any future default enablement:

- a representative real-user or anonymized large-corpus benchmark;
- scale tests for large claim/evidence/graph populations;
- long-duration soak tests and operational drift measurements;
- an explicit cold-start and steady-state latency SLO;
- live external-provider reliability testing across multiple Ollama/model versions;
- Linux as a formal semantic reference platform rather than best-effort compatibility;
- automatic migration and rollback UX for ordinary users;
- trained LTR or cross-encoder promotion with locked-test statistical significance;
- online feedback learning or automatic model training.

The current synthetic P95 includes cold-start cost and must not be presented as a production latency promise.

## Completion judgement

### Against the defined `0.2.18` Hybrid-v1 patch target

**Core implementation: complete.**

**Cross-platform acceptance: complete.**

**Release convergence: in progress.** The remaining blockers are version/document alignment, exact-final-head evidence, and the post-merge candidate run—not missing Hybrid retrieval architecture.

### Against a broader "turn it on by default for all users" expectation

**Not complete, intentionally.** The feature lacks the real-corpus, scale, soak, latency-SLO, migration-UX, and external-provider evidence required for responsible default enablement.

The correct expected release result is therefore:

> Ship `hybrid-v1` as an explicitly authorized, disabled-by-default Ledger-v1 retrieval profile with strong deterministic safety and macOS/Windows parity evidence; do not present it as a universally production-proven default retrieval replacement.
