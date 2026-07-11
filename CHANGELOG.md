# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

### Added
- Added contributor, support, roadmap, documentation-index, and community governance files.
- Added architecture, data-model, and threat-model documentation grounded in the current implementation.
- Added safe, isolated CLI and local text parsing examples with automated tests.
- Added CodeQL analysis, Python dependency auditing, and dependency-change review workflows.
- Added Python 3.11 coverage report artifacts.
- Added installed-wheel smoke coverage for packaged MCP resources, the Absorb text parser, and persisted `ms8 ask` records.
- Added Windows installed-wheel smoke validation under Unicode and space-containing paths.
- Added CycloneDX JSON SBOM generation to dependency security audits.

### Changed
- Enforced Dependency Review for high- and critical-severity dependency changes.
- Enforced `pip-audit` failures for known vulnerabilities or incomplete dependency collection.
- Strengthened release candidate and isolated package validation.
- Made release candidate branch matching and artifact naming version-agnostic.
- Standardized the active development branch on `main`.
- Enforced an 80% line-coverage baseline on the Python 3.11 CI job.
- Updated core GitHub Actions to current Node 24-compatible major versions before commit-SHA pinning.

### Fixed
- Restored Windows CLI startup by providing the self-check runner with the narrow file-lock compatibility layer it requires.
- Kept cross-platform release smoke output and isolated paths UTF-8-safe on Windows.

### Security
- Added least-privilege workflow permissions and scheduled code/dependency scanning.

## [0.2.15] - 2026-07-04

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
