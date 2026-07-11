# MS8 Third-Batch Memory Safety Plan

## Goal

Add a reliability layer that makes memory origin, confidence, action eligibility, and recovery behavior explicit without turning MS8 into a task orchestrator.

## Product boundary

- MS8 records and governs context memory.
- A connected AI assistant may execute work, but MS8 does not execute user tasks through remembered facts.
- Recall, injection, and action eligibility remain separate decisions.
- Human authorization remains authoritative for external actions.

## Phase 1 — Verifiable memory provenance

Add backward-compatible provenance metadata with:

- source kind and stable source reference;
- content digest;
- creator and recorder identity classes;
- observed, recorded, and validity timestamps;
- transformation chain and parent record references;
- verification state and confidence;
- schema version.

Acceptance criteria:

- new canonical records contain a valid provenance object;
- existing records remain readable;
- backfill is additive, idempotent, dry-run capable, and auditable;
- unknown fields survive repair and migration.

## Phase 2 — Pre-action governance

Add a structured `pre_action_check` decision surface.

Acceptance criteria:

- `can_recall` and `can_inject` never imply `can_act_on`;
- default canonical records cannot authorize actions;
- action decisions include allow/deny, reason codes, required confirmation, and supporting record IDs;
- unverified, expired, revoked, sensitive, or low-confidence records cannot authorize an action;
- the interface is reusable by CLI, MCP, and other adapters without granting execution capability.

## Phase 3 — Explainable low-confidence refusal

Acceptance criteria:

- policy filtering records per-reason counts instead of only a total blocked count;
- low-confidence or unverified memory returns a structured refusal/degradation reason;
- explicit user facts and verified records retain normal recall behavior;
- refusal does not leak secret or credential content.

## Phase 4 — Recovery chaos coverage

Add deterministic fault-injection tests for:

- corrupted JSONL and SQLite source data;
- tampered archives and manifests;
- interrupted restore before and during atomic replacement;
- pre-restore backup failure;
- partial target state and retry;
- recovery/audit evidence after failure.

Acceptance criteria:

- tests use isolated temporary runtime roots;
- failed restoration never reports success;
- pre-existing data remains recoverable;
- temporary restore files are cleaned or safely recoverable;
- retry after a simulated interruption produces a valid runtime.

## Delivery gates

Each phase must include direct unit tests, compatibility tests, documentation updates, and full repository CI. No automatic PyPI publication is introduced.
