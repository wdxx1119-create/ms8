# MS8 Data Model

This document describes the current local data model and compatibility expectations. MS8 is in Alpha; internal and derived schemas may change. Back up important data before upgrades.

## 1. Runtime roots

MS8 resolves its runtime locations through `src/ms8/paths.py` and creates them through `src/ms8/runtime.py`.

The main path overrides are:

- `MS8_HOME` — root directory.
- `MS8_DATA_DIR` — primary data directory.
- `MS8_CONFIG_DIR` — configuration directory.
- `MS8_LOG_DIR` — log directory.

When overrides are not provided, MS8 uses the configured/default user location. Code, tests, and examples must use the path helpers rather than constructing a separate `~/.ms8` path.

`ensure_runtime_dirs()` returns paths for:

- root, data, config, logs, health, and backups;
- the active memory record file;
- activity and maintenance-window state;
- compression state;
- non-canonical quarantine;
- repair audit records;
- the primary runtime configuration file.

## 2. Canonical memory record

The canonical record policy is implemented in `src/ms8/record_policy.py`.

A newly constructed canonical record contains at least:

```json
{
  "id": "uuid",
  "text": "normalized user-visible text",
  "normalized_text": "normalized text",
  "category": "general",
  "status": "accepted",
  "source": "ask",
  "created_at": "ISO-8601 UTC timestamp",
  "meta": {
    "admission": "ms8_write_guard_v1"
  },
  "scope": "personal",
  "authority": "user_explicit",
  "sensitivity": "private",
  "can_recall": true,
  "can_inject": true,
  "can_act_on": false
}
```

This example is illustrative. Consumers must tolerate additional fields and should not assume field ordering.

### Required fields

Current validation requires:

- `id`
- `normalized_text`
- `category`
- `status`
- `source`
- `meta`, including `meta.admission`
- `scope`
- `authority`
- `sensitivity`
- `can_recall`
- `can_inject`
- `can_act_on`

The permission flags must be booleans. A `system_debug` record cannot be marked for normal context injection.

### Text normalization

Text is normalized by collapsing repeated whitespace and trimming the result. `text` and `normalized_text` currently start with the same normalized value, but callers should treat `normalized_text` as the comparison/search normalization surface.

### Categories and scope inference

Current default construction can classify material such as:

- general memory;
- user preference;
- product/project decision;
- system diagnostic;
- experimental/Labs note.

Scope, authority, sensitivity, and permissions are inferred from source and content signals, then validated. This is policy behavior, not a general-purpose content taxonomy.

## 3. Record status state machine

Allowed statuses are:

- `candidate`
- `short_term`
- `accepted`
- `verified`
- `pending_review`
- `quarantined`
- `stale`
- `superseded`
- `revoked`

Status changes are constrained. Examples:

- `candidate` may become short-term, accepted, pending review, quarantined, or revoked.
- `accepted` may become verified, pending review, stale, superseded, revoked, or quarantined.
- `pending_review` may become accepted, verified, quarantined, or revoked.
- `quarantined` may return to pending review or become revoked.
- `revoked` is terminal.

Code must use the policy transition helper rather than directly replacing `status` without validation.

## 4. Retrieval permissions

The record includes three explicit capability flags:

- `can_recall` — record may be returned in governed retrieval.
- `can_inject` — record may be inserted into generated context.
- `can_act_on` — record may authorize downstream action; the default canonical constructor sets this to `false`.

These flags are not the only eligibility checks. Status, quarantine, review state, expiry, sensitivity, source restrictions, and requester context may further restrict a record.

## 5. JSONL streams

The active memory file is normally resolved by the engine and otherwise falls back to `data/memories.jsonl`.

JSONL is used for append-oriented streams because each line is independently parseable and can be audited or quarantined without rewriting the complete history.

Rules for JSONL writers:

1. Write UTF-8.
2. Write one complete JSON object per line.
3. Use canonical constructors and validators.
4. Do not append malformed objects to the active record file.
5. Send invalid/non-canonical material to quarantine with an explicit reason.
6. Flush/close files before secondary index updates depend on them.

### Non-canonical quarantine

The runtime maintains `memory/noncanonical_quarantine.jsonl`. Quarantine rows wrap the original record with a timestamp and reason. They are evidence for review or repair and must not be treated as active memory.

### Repair audit

Repair operations append to `memory/logs/repair_ops_audit.jsonl`. Repair tooling should record what was proposed or changed, not silently rewrite history.

## 6. Runtime configuration and state

### Root configuration

`config.json` contains operational policy defaults such as:

- governance risk thresholds;
- deduplication behavior;
- archive path for automatically superseded duplicates;
- governance SLO authority settings;
- Labs enablement.

Unknown keys should be preserved where possible. Configuration migrations must be explicit and documented.

### Compression state

`memory/compression_state.json` tracks compression/maintenance status, last run time, and result metadata. It is operational state, not a replacement for canonical records.

### Maintenance window

Health state can include `maintenance_window.json`, with flags that pause session ingestion, maintenance writes, review writes, or compression writes. Callers should check the runtime helper rather than reading this file ad hoc.

### Activity and health

Health and activity files are derived operational signals. They may be regenerated or replaced and must not be used as the sole source of user memory.

## 7. SQLite stores

MS8 uses SQLite for structured lookup and subsystem state. The exact database depends on the component.

### Engine stores

Engine SQLite stores support indexed lookup, operational metadata, and related structures. Whether a table is authoritative or rebuildable depends on the component; code should use the owning repository/store class.

### Absorb repository

The Absorb repository uses SQLite with WAL mode and foreign-key enforcement. It stores concepts including:

- registered sources and authorization metadata;
- discovered files and fingerprints;
- parse state and extracted document metadata;
- review/submission state;
- quarantine entries;
- source and operation events.

Absorb data is staging and provenance data. A parsed Absorb document becomes canonical memory only after the governed submission path accepts it.

### Knowledge graph

The knowledge graph uses SQLite-backed graph structures and supports isolation/rebuild checks. Direct SQL writes outside graph APIs can break graph consistency and are not supported.

## 8. Retrieval indexes

Whoosh and semantic indexes accelerate retrieval. They are derived artifacts and should be rebuildable from supported canonical inputs.

Index code must account for:

- removed or revoked records;
- superseded records;
- permission/status changes;
- failed or partial updates;
- schema/version changes.

An index hit is not automatic authorization to return a record. Record-policy filtering remains required after candidate retrieval.

## 9. Knowledge graph data

Graph nodes and relations represent derived entities, concepts, and associations. They may carry source/record references required for traceability.

Graph maintenance must preserve:

- linkage to supported source records;
- isolation of invalid or orphaned graph data;
- deterministic rebuild or repair where available;
- the same recall/injection restrictions as the underlying memory.

The graph must not be used to reconstruct or expose content that the source record is no longer allowed to reveal.

## 10. Backups

Backups are recovery snapshots, not live databases. A complete backup should include the authoritative records and the configuration/provenance needed to restore them. Derived indexes can be omitted only when the restore procedure reliably rebuilds them.

Restore procedures should:

1. Validate archive paths and prevent path traversal.
2. Restore into a temporary or explicitly selected target.
3. Validate canonical records before activation.
4. Rebuild derived indexes when needed.
5. Produce an audit/result report.
6. Keep the previous state until the restore is confirmed.

## 11. Compatibility policy during Alpha

Until a formal versioned schema is introduced:

- Additive fields are preferred over destructive changes.
- Readers should ignore unknown fields unless a security boundary requires rejection.
- Required-field or status changes must include migration logic and tests.
- Changes that alter recall/injection eligibility are security-relevant.
- Release notes and `CHANGELOG.md` must describe user-visible data changes.
- A migration must be restartable or have a documented rollback/recovery path.

## 12. Do not do this

Contributors must not:

- append raw dictionaries directly to the canonical record file without validation;
- promote quarantine rows by copying them into active JSONL;
- bypass status transition checks;
- treat a search-index hit as authorization;
- make logs, dashboards, or caches authoritative memory;
- write examples or tests against the real user runtime directory;
- silently migrate or delete data without backup, dry-run, and audit behavior.
