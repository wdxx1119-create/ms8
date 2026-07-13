# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

## [0.2.17] - 2026-07-13

### Added
- Added the opt-in Memory Ledger v1 foundation with append-only lifecycle decisions.
- Added independent recorded, observed, and valid-time query support.
- Added rebuildable SQLite, Search, FTS, Vector, and Graph projections.
- Added macOS and Windows validation coverage for Ledger v1.

### Changed
- Preserved existing CLI and MCP behavior while adding explicit Ledger query, context, explain, rebuild, migration, and lifecycle-operation surfaces.
- Unified durable atomic writes and cross-process file locking across macOS and Windows.
- Kept Memory Ledger v1 disabled by default.

### Fixed
- Fixed Windows SQLite projection rebuilds by explicitly releasing read handles before atomic replacement.
- Fixed Windows atomic file replacement, Unicode paths, and PowerShell CLI/MCP subprocess behavior.
- Fixed cross-platform typing and MCP resource compatibility issues.

### Security
- Required verified PolicyEngine grants for automated lifecycle changes.
- Kept incomplete, invalid, or hash-broken Ledger transactions fail-closed.
- Preserved explicit migration backup, rollback, audit, and physical-purge residual reporting.

## [0.2.16] - 2026-07-11

0.2.16 was prepared and validated in the repository but was not published to PyPI.
Its changes are included in 0.2.17.

### Added
- Added contributor, support, roadmap, documentation-index, and community governance files.
- Added architecture, data-model, and threat-model documentation grounded in the current implementation.
- Added safe, isolated CLI and local text parsing examples with automated tests.
- Added an end-to-end smoke workflow for the safe examples.
- Added CodeQL analysis, Python dependency auditing, and dependency-change review workflows.
- Added Python 3.11 coverage report artifacts.
- Added installed-wheel smoke coverage for packaged MCP resources, the Absorb text parser, and persisted `ms8 ask` records.
- Added Windows installed-wheel smoke validation under Unicode and space-containing paths.
- Added a CycloneDX JSON SBOM generated from the clean installed-wheel environment for each release candidate.
- Added regression tests that protect immutable Action references and supply-chain workflow contracts.
- Added the `ms8-recovery` entry point for verified full-runtime backup, archive verification, restore planning, restore application, format status, and migration execution.
- Added an explicit runtime-format manifest and stepwise migration registry with pre-migration backup and audit records.
- Added backup/restore roundtrip, SQLite snapshot, checksum-tamper, path-traversal, migration, and release-contract tests.
- Added explicit `llm`, `absorb`, `ocr`, `policy`, and `full` installation profiles, while retaining `absorb-ocr` as a compatibility alias.
- Added clean-room CI validation for every supported installation profile.
- Added a weekly macOS/Windows boundary matrix for Python 3.10 and 3.13.
- Added GitHub build provenance attestations for wheel and source distribution, plus an SBOM attestation bound to the wheel.
- Added Trusted Publishing preparation documentation without enabling automatic PyPI publication.
- Added a temporary compatibility workflow for legacy branch-protection check names until the repository ruleset is migrated to the current CI contexts.
- Added backward-compatible provenance metadata for canonical memory records, including source identity, content digest, timestamps, transformation lineage, verification state, and confidence.
- Added a non-executing MCP `pre_action_check` surface with structured decisions and reason codes.
- Added deterministic recovery fault-injection coverage for interrupted restores, corrupted data, tampered archives, and retry behavior.

### Changed
- Enforced Dependency Review for high- and critical-severity dependency changes.
- Strengthened release candidate and isolated package validation.
- Made release candidate branch matching and artifact naming version-agnostic.
- Standardized the active development branch on `main`.
- Enforced an 80% line-coverage baseline on the Python 3.11 CI job.
- Made Python dependency audit findings block dependency and audit-workflow changes.
- Isolated the dependency audit target from the `pip-audit` tool environment.
- Unified local, CI, and release-candidate pytest collection through `pyproject.toml` test paths.
- Replaced the `.venv/bin/python`-specific release checklist with a cross-platform Python gate that validates tests, coverage, wheel, sdist, SBOM, vulnerabilities, and checksums.
- Removed Ollama from the mandatory core dependency set; local-model support is now installed with `ms8[llm]` or `ms8[full]`.
- Made the `ocr` profile include the complete Absorb parser dependency set.
- Limited Release Candidate validation to `candidate/**` or `rc-*` pushes, or explicit manual dispatch.
- Reduced repeated macOS candidate testing to one boundary job and moved pure-Python artifact construction and auditing to Ubuntu.
- Added CI concurrency cancellation and explicit job timeouts.
- Required final candidate evidence and attestations to be regenerated from the exact post-merge commit that will receive the release tag.
- Kept PyPI publication as a separate maintainer action for this release.
- Made recall and injection filtering expose per-reason policy counts and low-confidence degradation evidence.
- Made provenance repair additive, idempotent, dry-run capable, and auditable while preserving older canonical records.
- Preserved explicit MCP browsing of governed product-decision records while keeping search-intent filtering on query-driven recall.

### Fixed
- Restored Windows CLI startup with platform-specific non-blocking self-check locks (`msvcrt` on Windows and `fcntl` on POSIX).
- Kept cross-platform release smoke output and isolated paths UTF-8-safe on Windows.
- Removed the local 75% coverage exception so local release validation matches the repository's 80% baseline.
- Prevented optional Ollama and document/OCR parser dependencies from leaking into the core wheel installation.
- Preserved the legacy MCP source identifiers `mcp:submit` and `mcp:batch_submit` for unnamed clients while recording explicit client identities when provided.

### Security
- Added least-privilege workflow permissions and scheduled code/dependency scanning.
- Pinned third-party GitHub Actions to full commit SHAs while retaining version comments for automated updates.
- Included the release SBOM in SHA-256 checksums and retained release-candidate artifacts.
- Added a strict vulnerability gate for the clean environment containing the installed release wheel.
- Added SHA-256 verification, undeclared-file rejection, path-traversal rejection, SQLite-consistent snapshots, pre-restore backup, atomic file replacement, and restore/migration audit logs.
- Added OIDC-backed GitHub artifact attestations with explicit `id-token: write` and `attestations: write` permissions only in the candidate workflow.
- Required explicit supporting memory IDs, verified user-explicit authority, exact action-scope matching, uniform evidence eligibility, and explicit human confirmation before an action decision can be allowed.
- Kept action checks advisory only: MS8 reports a decision and never executes the external action.

## [0.2.15] - 2026-07-04

> This release was yanked from PyPI because a Windows-only line was published under the shared `ms8` package name.

### Fixed
- Restored the `ms8 absorb project-memory ...` CLI route for the packaged project-memory workflow.
- Treated empty validation-suite state in fresh runtimes as a warning instead of a hard self-check failure.

### Packaging
- Excluded test fixtures from source distributions so release artifacts avoid synthetic secret samples and stay cleaner for downstream scanners.

## [0.2.14] - 2026-06-27

### Added
- Added the complete Project Memory workflow, including scan, index, build, submit, watch, search, health, and service support.
- Expanded the MCP-facing memory surface for richer daily agent context.

### Changed
- Improved `doctor`, `watch`, service, and runtime-health interpretation and follow-up guidance.
- Bound service startup to the active Python environment instead of relying on an unrelated `ms8` executable from `PATH`.
- Strengthened Absorb parsing, local-material processing, and runtime-mode handling.

### Fixed
- Kept the default adapter registry template portable and free of local absolute-path metadata.
- Tightened typed exception handling, cleanup behavior, and degraded-state reporting.
- Corrected resource cleanup for Absorb SQLite handles and Ollama provider responses.

## [0.2.13] - 2026-06-16

### Fixed
- Aligned package, runtime, README, and changelog version metadata with `pyproject.toml`.
- Aligned README license display with the repository's `GPL-3.0-or-later` license metadata.
- Included MCP YAML and JSON resource files in wheel/sdist package data.

### Changed
- Replaced local machine-specific adapter registry probe paths with portable template metadata.

## [0.2.12] - 2026-06-08

### Changed
- Restored the published Python support range to the actually verified window: `3.10` to `3.13`.
- Removed private policy-core build/release workflows from the main MS8 repository so the public/private boundary is clean.
- Tightened CI to the supported Python matrix and fixed a mypy regression in the admission pipeline.

### Fixed
- Removed stray duplicate packaging artifacts (`* 2`) before build so release artifacts stay clean.
- Kept the main release path stable while preserving private policy-core distribution outside the public MS8 repo.
- Published `ms8-policy-core` `0.1.1` as an optional closed-backend enhancement with encrypted private-key detection, while keeping the default MS8 install cross-platform.

## [0.2.0] - 2026-05-31

### Added
- Pluggable policy engine interface with open/closed backends.
- Policy backend loader with strict fail-closed mode support.
- Policy attack sample report and governance gate integration.
- Expanded automated test suite and coverage reporting workflow.
- Release isolation and artifact inspection scripts.

### Changed
- Memory admission path now prefers policy-engine decisions with safe local fallback.
- Doctor/dashboard now expose policy engine and policy-attack sample health signals.
- Governance report now includes policy attack sample status in layered health.
- CI workflow now enforces mypy, ruff, pytest coverage reporting, and doctor smoke checks.

### Security
- Closed backend contract validation and strict policy backend loading paths.
- Governance gate supports policy-attack failure as release blocker.

## Version Strategy

- `0.2.x`: policy-engine contract stabilization and release engineering hardening.
- `0.3.x`: closed strategy enhancement, retrieval/governance refinement, and distribution polish.
- `1.0.0`: production validation milestone after sustained external usage and operational stability.
