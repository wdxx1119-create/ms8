# MS8 Threat Model

This document identifies the security and privacy assumptions of the current MS8 design. It complements [SECURITY.md](../SECURITY.md), which explains private vulnerability reporting.

MS8 is a governed local memory system. It reduces unsafe memory operations; it is **not** an operating-system sandbox, malware scanner, endpoint protection product, or substitute for host access control.

## 1. Security objectives

MS8 aims to protect:

- confidentiality of personal memory, imported documents, configuration, and credentials;
- integrity of canonical records, governance state, review decisions, indexes, graph data, backups, and audit history;
- availability and recoverability of local memory;
- enforcement of record state, source authorization, recall/injection permissions, and high-risk operation confirmation;
- traceability of imported material and maintenance/repair actions;
- separation between active memory, pending review, and quarantine.

## 2. Assets

High-value assets include:

1. Canonical memory JSONL and associated metadata.
2. SQLite databases and knowledge graph state.
3. Absorb source authorization, parsed content, review state, and event history.
4. Encryption material, API keys, MCP/client configuration, and provider credentials.
5. Backups and exported archives.
6. Logs, reports, health data, and workflow artifacts that may reveal paths or operational details.
7. Governance settings, policy-engine configuration, review decisions, and permission flags.
8. Release artifacts and dependency metadata.

## 3. Trust boundaries

### Boundary A: operating system and local account

MS8 trusts the operating system to enforce filesystem and process permissions. A process running as the same user may be able to read or modify local MS8 files unless additional encryption and OS controls prevent it.

MS8 does not defend against a fully compromised administrator/root account.

### Boundary B: CLI input

CLI text, file paths, flags, environment variables, and configuration are untrusted inputs. Commands must validate paths, avoid shell interpolation, and require explicit confirmation or dry-run for destructive actions.

### Boundary C: MCP clients and adapters

An MCP client may be buggy, over-permissioned, or malicious. Client identity/configuration does not automatically authorize every memory operation. The MCP layer must use governed service interfaces and preserve source/request context.

### Boundary D: Absorb sources

Files from authorized directories remain untrusted content. Authorization to read a directory does not mean every file is safe, relevant, or approved for memory insertion.

Potential content includes malformed documents, oversized files, embedded instructions, prompt injection, secrets, PII, and misleading provenance.

### Boundary E: model providers

Local and remote model output is untrusted advice. Models can hallucinate classifications, follow injected instructions, or return sensitive text. Model output must not directly authorize high-risk actions or bypass deterministic record policy.

Remote providers additionally create a data-egress boundary. Users must understand what content is sent externally.

### Boundary F: dependencies and release pipeline

Python packages, GitHub Actions, build tools, and release artifacts are supply-chain inputs. A compromised dependency or mutable workflow action can affect builds or runtime behavior.

### Boundary G: backups and exports

Backups concentrate sensitive information and may outlive the active runtime. An export destination is outside MS8's control after creation.

## 4. Threat actors

The model considers:

- accidental user error;
- a malicious or compromised MCP client;
- malicious content in imported files;
- a compromised model/provider response;
- another local process running as the same user;
- a dependency or CI supply-chain compromise;
- an attacker who obtains a backup, log, or workflow artifact;
- a contributor who unintentionally bypasses governance through a new code path.

A fully compromised kernel, administrator account, or physical host is outside the primary protection boundary, although encryption and backup hygiene may reduce impact.

## 5. Threats and controls

### T1. Unauthorized memory write

**Scenario:** A client or automation writes a record without review, source metadata, or admission checks.

**Controls:**

- canonical record construction and validation;
- admission/governance pipeline;
- explicit record status and source metadata;
- pending-review and quarantine states;
- MCP service reuse of the governed memory interface;
- tests that detect alternate direct-write paths.

**Residual risk:** A contributor can still introduce a bypass in production code. Code review, CODEOWNERS, tests, and security review remain required.

### T2. Unauthorized recall or context injection

**Scenario:** A search result exposes a revoked, quarantined, expired, source-restricted, or sensitive record.

**Controls:**

- `can_recall` and `can_inject` flags;
- status and transition policy;
- post-retrieval policy filtering;
- system-debug records cannot be normal-injection records;
- source/provenance metadata;
- separation of quarantine from active memory.

**Residual risk:** New retrieval surfaces may forget to apply the shared policy. Every new API/MCP/resource endpoint needs negative authorization tests.

### T3. Prompt injection through Absorb

**Scenario:** An imported document contains instructions intended to control an AI assistant or change memory policy.

**Controls:**

- explicit authorized source roots;
- parsing as data rather than command execution;
- governance and risk decisions after parsing;
- review/quarantine stages;
- automatic final write remains conservative;
- provenance and source events support rollback.

**Residual risk:** Semantic classifiers or human reviewers may still accept misleading content. Imported text must never be treated as trusted system instructions.

### T4. Malicious or malformed file

**Scenario:** A file exploits a parser, consumes excessive resources, or triggers unsafe path handling.

**Controls:**

- parser allowlists and explicit optional dependencies;
- parse failure states instead of silent promotion;
- source exclusions and high-risk root confirmation;
- temporary/isolated processing where supported;
- dependency scanning and parser updates;
- quarantine and event logging.

**Residual risk:** Third-party parser vulnerabilities remain possible. High-risk file formats should be processed with least privilege and resource limits.

### T5. Path traversal or runtime-root confusion

**Scenario:** An archive, source path, environment variable, or legacy path causes writes outside the intended runtime or splits state across roots.

**Controls:**

- centralized path helpers;
- runtime-root split-brain checks;
- explicit environment overrides;
- safe archive/restore path validation;
- CI coverage for space and Unicode paths;
- tests and examples use temporary roots.

**Residual risk:** A user who explicitly points MS8 at an unsafe location assumes the filesystem consequences. Commands should still reject obviously dangerous roots where applicable.

### T6. Concurrent or partial maintenance operation

**Scenario:** Two self-check/repair processes run together, or a process dies while updating state.

**Controls:**

- non-blocking self-check file lock (`fcntl` on POSIX, `msvcrt` on Windows);
- progress files and stale-run detection;
- append-oriented audit records;
- dry-run/preview for high-risk maintenance;
- rebuildable secondary indexes.

**Residual risk:** Filesystem/network-share locking semantics differ. Shared runtime directories across hosts are not assumed safe without additional coordination.

### T7. Secret or PII leakage in logs and reports

**Scenario:** Memory text, credentials, local paths, or imported documents appear in logs, Issues, CI artifacts, or support bundles.

**Controls:**

- support policy requires redaction;
- examples and tests use synthetic data;
- workflows use temporary isolated homes;
- secret scanning and push protection;
- report-only artifacts have limited retention;
- security-sensitive issues use private advisories.

**Residual risk:** Application exceptions and third-party libraries may include user data. Logging changes require privacy review.

### T8. Backup disclosure or unsafe restore

**Scenario:** A backup is copied to an insecure destination or a crafted archive writes outside the restore directory.

**Controls:**

- local ownership and explicit destinations;
- archive path validation;
- checksums and validation before activation;
- documented credential and backup hygiene;
- restore into a temporary/selected target;
- keep previous state until validation succeeds.

**Residual risk:** MS8 cannot control an export after it leaves the runtime. Users must encrypt and protect backup media.

### T9. Dependency or workflow compromise

**Scenario:** A vulnerable Python package or compromised GitHub Action affects runtime or builds.

**Controls:**

- Dependabot configuration;
- strict Dependency Review for high/critical changes;
- CodeQL;
- scheduled `pip-audit` report;
- least-privilege `GITHUB_TOKEN` permissions;
- clean build/install verification and checksums;
- release artifacts built from reviewed commits.

**Residual risk:** Report-only audits do not automatically block every existing vulnerability. Action tags may be mutable until pinned to commit SHAs.

### T10. Model/provider data exfiltration

**Scenario:** Sensitive memory is sent to a remote model unexpectedly.

**Controls:**

- local-first baseline and explicit optional providers;
- degraded local/rule-based behavior;
- sensitivity and permission metadata;
- provider configuration remains separate from canonical storage.

**Residual risk:** A configured remote provider is an external processor. Provider-specific minimization and consent require continued review.

### T11. Unsafe automated action

**Scenario:** Remembered content becomes authority to execute a command or make an irreversible change.

**Controls:**

- canonical records default `can_act_on` to `false`;
- memory recall and action authorization are separate concepts;
- dry-run and confirmation for destructive maintenance;
- governance/control gates for automated operations.

**Residual risk:** Integrating applications may misuse recalled text as instruction. MCP clients and agents must preserve the distinction between context and authorization.

## 6. Security invariants for contributors

A change must not violate these invariants:

1. Quarantine and pending-review data are not ordinary active memory.
2. Search/index results are candidates, not authorization.
3. A model decision alone cannot bypass deterministic policy.
4. A new CLI/MCP surface must use centralized path and governance helpers.
5. Destructive changes require explicit user intent and preferably preview/dry-run.
6. Tests must not access the user's real runtime.
7. Secrets and authentic user memory must not enter fixtures, logs, examples, or artifacts.
8. Optional services must fail explicitly or degrade safely; they must not silently become authoritative storage.
9. Windows, macOS, and Linux behavior must avoid platform-only imports on common startup paths.
10. Security-relevant schema changes require migration and negative tests.

## 7. Security testing expectations

Security-sensitive PRs should consider:

- invalid/missing canonical fields;
- forbidden state transitions;
- revoked/quarantined recall attempts;
- source permission failures;
- path traversal and symlink behavior;
- Unicode, long, and space-containing paths;
- concurrent locks and interrupted operations;
- malicious/oversized Absorb files;
- prompt-injection content treated as data;
- log/artifact redaction;
- clean installed-wheel behavior, not only editable source-tree tests;
- dependency and CodeQL findings.

## 8. Incident response

For a suspected vulnerability:

1. Do not open a public Issue with exploit details.
2. Use the repository's private GitHub Security Advisory channel.
3. Include affected version/commit, impact, minimal reproduction, and mitigation ideas.
4. Revoke and rotate any exposed credential immediately.
5. Preserve relevant evidence without publishing real memory or secrets.
6. Prepare and validate a fix before coordinated public disclosure.

See [SECURITY.md](../SECURITY.md) for response targets and disclosure policy.

## 9. Open security work

The following remain active hardening areas:

- make installed dependency audit blocking after baseline triage;
- pin workflow actions to immutable commit SHAs;
- generate and retain an SBOM for release candidates;
- expand negative authorization tests across MCP/resources;
- formalize schema migration and rollback guarantees;
- add parser resource limits and hostile-file fixtures where safe;
- document remote-provider data minimization per provider.
