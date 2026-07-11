# MS8 Data Model

This document describes the current Alpha data model and storage roles. It is intended for maintainers, reviewers and users planning backup or migration work.

## Authority hierarchy

Not every local file has equal authority.

1. **Canonical memory records** are the primary governed memory stream.
2. **Review, quarantine and audit records** preserve decisions and rejected or repair-related evidence.
3. **Absorb repository data** tracks local files before and during governed submission.
4. **Knowledge graph, search indexes, summaries and caches** are derived structures.
5. **Health and operational state** supports diagnosis but is not a substitute for memory backup.

Derived stores should not silently overwrite canonical records merely because their representation differs.

## Runtime roots

The default runtime root is:

```text
~/.ms8
```

A legacy runtime at `~/.ms8_runtime` may be selected when it contains stronger existing data markers. Explicit environment variables take priority:

| Variable | Purpose |
|---|---|
| `MS8_HOME` | Overall runtime root |
| `MS8_DATA_DIR` | General data directory |
| `MS8_CONFIG_DIR` | Configuration directory |
| `MS8_LOG_DIR` | Log directory |

The runtime also creates or resolves directories for backups, health data, configuration and memory-engine data.

## Representative runtime layout

The exact set of files depends on enabled features, but a typical runtime may contain:

```text
<MS8_HOME>/
├── config.json
├── config.yaml
├── backups/
├── config/
├── data/
│   └── memories.jsonl              # compatibility/fallback location
├── health/
│   ├── activity.json
│   └── maintenance_window.json
├── logs/
├── memory/
│   ├── auto_memory_records.jsonl   # canonical engine record stream
│   ├── auto_memory_index.json      # derived index
│   ├── knowledge_graph.db          # derived/structured graph store
│   ├── noncanonical_quarantine.jsonl
│   ├── compression_state.json
│   └── logs/
│       └── repair_ops_audit.jsonl
└── absorb/
    ├── absorb.sqlite
    ├── events.jsonl
    ├── events.1.jsonl ...
    └── quarantine/
```

Do not infer that a file is authoritative solely from its extension. Use the roles in this document and the code path that produced it.

## Canonical memory record

`ms8.record_policy.build_canonical_record` currently produces records with fields similar to:

```json
{
  "id": "uuid",
  "text": "normalized memory text",
  "normalized_text": "normalized memory text",
  "category": "general",
  "status": "accepted",
  "source": "ask",
  "created_at": "2026-07-11T00:00:00+00:00",
  "scope": "personal",
  "authority": "user_explicit",
  "sensitivity": "private",
  "can_recall": true,
  "can_inject": true,
  "can_act_on": false,
  "meta": {
    "admission": "ms8_write_guard_v1"
  }
}
```

Additional engine or migration fields may be present. Consumers must tolerate additive fields and should not discard unknown metadata during repair or migration.

### Provenance object

New canonical records include a `provenance` object with a content digest, source kind/reference, creator/recorder classes, observation and recording timestamps, validity interval, parent record IDs, transformation chain, verification state, confidence, and provenance schema version.

Provenance is additive for backward compatibility: older records remain readable, while repair/backfill can add the object idempotently. A provenance object whose content digest does not match the canonical text is invalid and must not authorize recall, injection, or action.

`confidence` is evidence quality, not action permission. `can_act_on` remains independently false by default, and only a verified, explicit-user-authorized record with explicit confirmation may pass the pre-action gate.

## Required invariants

A canonical record must currently include:

- `id`
- `normalized_text`
- `category`
- `status`
- `source`
- `meta.admission`
- `scope`
- `authority`
- `sensitivity`
- boolean `can_recall`
- boolean `can_inject`
- boolean `can_act_on`

A `system_debug` record must not have `can_inject=true`.

The policy normalizes whitespace before storing text. Record identifiers are UUID strings in the default builder.

## Scope, authority and sensitivity

These dimensions have separate purposes.

### Scope

Representative scopes include:

- `personal`
- `project`
- `system_debug`
- `labs`

Scope controls where a record is relevant. Debug and Labs records have stricter injection behavior than ordinary personal records.

### Authority

Representative authorities include:

- `user_explicit`
- `user_implicit`
- `system_observed`
- `assistant_inferred`
- `tool_generated`

Authority describes where the claim came from. Lower-authority claims may require verification before normal recall or injection.

### Sensitivity

Representative sensitivities include:

- `private`
- `internal`
- `secret`
- `credential`

Current recall policy rejects `secret` and `credential` records from normal recall. This is a policy boundary, not a replacement for encryption, host security or secret rotation.

## Capability flags

The three capability flags are intentionally independent:

| Flag | Meaning |
|---|---|
| `can_recall` | The record may be returned through governed retrieval |
| `can_inject` | The record may be placed into an AI context when other policy checks pass |
| `can_act_on` | The record itself grants action authority |

The default canonical builder sets `can_act_on` to `false`. A remembered fact must not automatically authorize an external action.

## Record lifecycle

Current statuses are:

```text
candidate
short_term
accepted
verified
pending_review
quarantined
stale
superseded
revoked
```

Transitions are constrained. Representative examples:

- `candidate` may become `short_term`, `accepted`, `pending_review`, `quarantined` or `revoked`.
- `accepted` may become `verified`, `pending_review`, `stale`, `superseded`, `revoked` or `quarantined`.
- `pending_review` may become `accepted`, `verified`, `quarantined` or `revoked`.
- `revoked` is terminal in the current transition table.

Recall policy excludes candidate, pending-review, quarantined, stale, superseded and revoked records from normal recall. Expired records and records with `superseded_by` are also excluded.

## Append and quarantine behavior

Canonical writes are append-oriented JSONL operations. Invalid records are not supposed to be silently normalized into acceptance; validation failures can be written to quarantine with a reason and original record payload.

The runtime initializes:

- `memory/noncanonical_quarantine.jsonl`
- `memory/logs/repair_ops_audit.jsonl`

Repair tools should preserve evidence of what changed, why it changed and which source record was affected.

## Engine fallback behavior

The unified engine normally resolves the canonical records file through bundled `MemoryCore`. If the engine is unavailable or cannot expose its records path, the runtime can use a compatibility location under `data/memories.jsonl`.

Fallback writes still call the canonical record policy. A fallback path is not permission to omit governance fields.

Backup and migration tools must inspect the active engine status and resolved records path rather than assuming one hard-coded location.

## Absorb repository

Absorb maintains a separate SQLite repository at:

```text
<MS8_HOME>/absorb/absorb.sqlite
```

Current tables include:

### `file_records`

Tracks a discovered file and its lifecycle:

- stable `file_id`
- canonical path and path hash
- content and quick hashes
- file type, size and timestamps
- first/last seen times
- status, risk and parse state
- source, MS8 record linkage and error text

### `chunks`

Tracks parsed or submitted portions:

- `chunk_id`
- parent `file_id`
- chunk index and hash
- text preview and token count
- status and risk
- submission time

### `ingest_jobs`

Tracks processing work:

- `job_id`
- `file_id`
- job type
- status and reason
- creation/update times

### `audit_events`

Tracks decisions and operational evidence:

- `event_id`
- event type
- file ID and path
- decision and reason
- creation time

Absorb uses SQLite WAL mode and a busy timeout. Its append-style event log rotates when it reaches the configured size.

## Absorb statuses

Representative states include:

```text
DISCOVERED
LOCAL_INDEXED
DUPLICATE
CHANGED
READY_FOR_PARSE
PARSED
READY_FOR_GOVERNANCE
PENDING_REVIEW
QUARANTINED
READY_FOR_MS8
SUBMITTED_TO_MS8
MS8_ACCEPTED
MS8_REJECTED
DELETED
ERROR
FILTERED
OCR_REQUIRED
```

The separation between `PARSED`, `READY_FOR_GOVERNANCE`, `SUBMITTED_TO_MS8` and `MS8_ACCEPTED` is important. File parsing alone does not authorize canonical memory storage.

## Derived stores

Knowledge graphs, search indexes, semantic caches and generated summaries improve retrieval but can become stale or inconsistent. They should be treated as derived unless a specific migration document says otherwise.

For each derived store, maintainers should define:

- source of truth
- rebuild procedure
- schema/version marker
- consistency check
- failure and fallback behavior
- backup requirement, if any

## Backup guidance

A minimum backup should include the active runtime root, especially:

- canonical memory record file
- configuration needed to interpret policy and paths
- review and quarantine state
- repair audit records
- Absorb repository when source provenance or pending work matters
- encryption or recovery material stored under the runtime, where applicable

Indexes can often be rebuilt, but preserving them may reduce recovery time. Never copy a live SQLite database without using a method that produces a consistent snapshot.

## Migration rules

A migration should:

1. detect the active source runtime and version
2. back up before modifying data
3. preserve unknown fields
4. validate every transformed canonical record
5. quarantine rather than discard invalid input
6. rebuild derived stores after canonical data succeeds
7. emit an audit record and summary
8. support dry-run when feasible
9. leave rollback instructions

## Compatibility status

MS8 is Alpha. The fields and locations above describe the current implementation, not a frozen long-term schema. Breaking changes should be recorded in the Changelog and accompanied by migration guidance before a release.
