# MS8 Threat Model

This threat model describes the current Alpha security boundaries of MS8. It complements [SECURITY.md](../SECURITY.md), which explains how to report vulnerabilities.

## Scope

In scope:

- local memory records and derived indexes
- CLI and maintenance commands
- MCP and adapter connections
- Absorb file discovery, parsing, review and submission
- configuration, logs, backups and audit data
- optional model/provider integration
- package, CI and release supply chain

Outside the current product scope:

- a hosted multi-tenant MS8 service
- operating-system compromise recovery
- guarantees against an administrator or root user who can read all local files
- security properties of third-party MCP clients, model services or document parsers beyond MS8's integration boundary

## Security objectives

MS8 aims to protect:

1. **Confidentiality**: memory, source documents, credentials and PII should not leak through logs, reports, contexts or package artifacts.
2. **Integrity**: records, governance fields, review decisions and derived stores should not be modified without detection or an auditable path.
3. **Availability and recoverability**: corruption or failed automation should not make recovery unnecessarily difficult.
4. **Authorization boundaries**: stored information should not automatically become recallable, injectable or actionable.
5. **Provenance**: the system should preserve where a record or document came from and what policy decision was applied.

## Assets

High-value assets include:

- canonical memory JSONL records
- encryption and recovery material
- user configuration and provider credentials
- Absorb source paths, hashes, parsed content and review state
- backups and migration snapshots
- review, quarantine and repair-audit data
- MCP client configuration and adapter registry
- release credentials and CI artifacts

Derived indexes may contain sensitive copies or summaries even when they are not authoritative.

## Actors

### Legitimate local user

Controls the host account and chooses which tools, folders and providers to connect. Accidental mistakes remain a major risk, especially destructive commands, broad folder authorization and publication of diagnostic output.

### Connected AI client or tool

Can invoke an allowed interface but must not be assumed to have unrestricted authority. A client may be buggy, compromised or manipulated by prompt injection.

### Malicious or untrusted document

A file processed by Absorb may contain prompt injection, malformed structures, decompression bombs, misleading metadata or sensitive content the user did not intend to store.

### Local unprivileged process

May attempt to read runtime files, race maintenance operations, replace configuration or write malicious records when host permissions are weak.

### Dependency or supply-chain attacker

May compromise a Python dependency, GitHub Action, package index account, release artifact or maintainer credential.

### Host administrator or malware

Can generally bypass application-level controls. MS8 cannot provide strong confidentiality against an attacker with full control of the user account or operating system.

## Trust boundaries

### Boundary 1: user or AI client to CLI/MCP

Input can contain instructions, memory text, queries and operational parameters. Treat it as untrusted until normalized, authorized and governed.

### Boundary 2: Absorb source directory to parser

Authorized directory access does not imply that every contained file is safe or suitable for memory. Discovery, parsing, governance and submission remain separate stages.

### Boundary 3: governed record stream to derived stores

Indexes and graphs should derive from validated records. A corrupted derived store must not become an unchecked source of truth.

### Boundary 4: local runtime to external provider

Any model or health-check integration can transmit data outside the host. Only the minimum necessary content should cross this boundary, and the user must explicitly configure the integration.

### Boundary 5: source repository to release artifact

CI, dependencies, GitHub Actions and package publishing can change what users install. Build and release evidence must be independently verifiable.

## Threats and mitigations

| Threat | Example | Current or required mitigation |
|---|---|---|
| Unauthorized memory write | An adapter appends JSON directly | Route writes through canonical admission and record validation; quarantine invalid records |
| Unsafe recall | A secret or revoked record appears in search | Enforce status, expiration, sensitivity, authority and scope checks before returning candidates |
| Unsafe injection | Debug or unreviewed data enters an AI prompt | Keep recall and injection decisions separate; `system_debug` and Labs records must not normally inject |
| Action escalation | A remembered instruction is treated as permission | Default `can_act_on=false`; require an independent action authorization layer |
| Prompt injection in documents | A local file instructs an agent to leak data | Treat parsed content as data, not authority; govern and review before submission or use |
| Overbroad file authorization | User selects a home or system root | Require explicit roots, exclusions and high-risk confirmation; keep automatic submission disabled by default |
| Path traversal or symlink escape | Scanner leaves the authorized root | Resolve canonical paths, verify containment and record provenance before processing |
| Malformed or oversized document | Parser consumes excessive memory or time | File-size limits, parser timeouts, error isolation, OCR separation and quarantine |
| Concurrent maintenance corruption | Two self-check or repair processes run | Non-blocking platform-specific file locks and progress records; audit stale-lock recovery |
| Record tampering | Local process edits JSONL or SQLite | Host permissions, validation, audits, consistency checks, backups and repair evidence |
| Log or report leakage | `doctor` output contains memory or tokens | Redact content, credentials, PII and absolute user paths; request minimal sanitized diagnostics |
| Backup leakage | Backup is copied to an insecure location | Treat backups as sensitive, encrypt where appropriate and document retention/removal |
| External provider disclosure | Query or memory is sent to a model API | Explicit configuration, minimum payload, provider documentation and a local/degraded alternative |
| Dependency compromise | Malicious package or Action update | Dependabot, strict Dependency Review, CodeQL, `pip-audit`, least-privilege workflow permissions and reviewed lock/update changes |
| Release substitution | User installs an unexpected artifact | Build wheel/sdist in CI, verify metadata, generate checksums and test installed artifacts in clean environments |
| Secret committed to repository | Token appears in source or fixtures | Secret scanning, push protection, private reporting and immediate credential rotation |

## Memory authorization model

MS8 separates four concepts:

1. **Stored**: the record exists locally.
2. **Recallable**: policy permits retrieval for a query.
3. **Injectable**: policy permits adding it to an AI context.
4. **Actionable**: an independent authorization permits an external action.

A secure integration must not collapse these states into one boolean.

Authority and sensitivity should be checked together. For example, a tool-generated private record can be less trustworthy than a user-verified private record even though both have similar confidentiality requirements.

## Absorb-specific risks

### Source authorization

Only folders intentionally selected by the user should be scanned. Exclusion patterns, symbolic links, mounted volumes and generated files must be considered when establishing the actual scope.

### Parser isolation

Parsers handle untrusted bytes. Failures should be contained to the affected file and recorded without writing unsafe content into canonical memory.

### Prompt injection

Document text must never be interpreted as system or developer instructions simply because it was parsed locally. Summaries and extracted claims require provenance and governance.

### Review and rollback

High-risk, ambiguous or sensitive material should remain pending or quarantined. Source identifiers and MS8 record links are needed for targeted rollback.

## MCP and adapter risks

- A connected client may send malformed requests or excessive queries.
- Client configuration files can expose command paths or environment variables.
- A client may request more context than it needs.
- Multiple clients may have different trust levels.

Adapters should use least privilege, validate request schemas, limit returned context and avoid embedding credentials in generated configuration.

## Local host assumptions

MS8 relies on the operating system for account isolation, filesystem permissions, process isolation and secure credential storage. Users should:

- keep the OS and Python environment patched
- use a dedicated virtual environment
- avoid world-readable runtime or backup directories
- protect device login and disk encryption
- avoid running MS8 as an elevated administrator unless required
- rotate credentials after suspected exposure

Application-level encryption cannot protect plaintext after an authorized process has decrypted it in a compromised host session.

## Supply-chain controls

Current repository controls include:

- pull-request review and required status checks
- Python 3.10–3.13 tests
- an 80% line-coverage baseline
- Windows, macOS and Linux installed-package checks
- CodeQL
- strict dependency-change review
- scheduled Python dependency audit
- Dependabot for Python and GitHub Actions
- least-privilege workflow tokens
- wheel and source-distribution verification

Residual risks include mutable Action version tags, package-index account compromise and vulnerabilities without a published advisory.

## Failure and recovery principles

Security-sensitive failures should prefer:

- fail closed for authorization and high-risk writes
- explicit degraded status for optional capabilities
- quarantine instead of silent deletion
- dry-run before destructive operations
- backup before migration or repair
- an audit record describing decisions and affected data

A failure to update an index must not silently rewrite or discard canonical memory.

## Security testing expectations

Changes to security-relevant paths should include tests for:

- invalid and boundary input
- denied status/sensitivity/authority combinations
- malformed files and parser errors
- temporary isolated paths
- Windows, macOS and Linux differences where applicable
- race and lock behavior
- package-resource presence after wheel installation
- redaction of logs and reports

Tests and examples must never use a contributor's real runtime directory.

## Residual risks

Known classes of risk that remain in Alpha:

- incomplete consistency and migration coverage across all derived stores
- parser and optional dependency vulnerabilities
- accidental broad directory authorization
- imperfect detection of sensitive content and prompt injection
- local malware or administrator access
- behavior changes in third-party MCP clients or model providers
- operational mistakes during manual publishing

## Reporting vulnerabilities

Do not open a public Issue for a vulnerability, leaked secret, privacy bypass or authorization weakness. Use the private GitHub Security Advisory channel described in [SECURITY.md](../SECURITY.md).

A report should include the affected version, impact, minimal reproduction, relevant platform and a sanitized proof of concept.
