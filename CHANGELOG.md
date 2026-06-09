# Changelog

All notable changes to this project are documented in this file.

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
- Expanded automated test suite and coverage enforcement workflow.
- Release isolation and artifact inspection scripts.

### Changed
- Memory admission path now prefers policy-engine decisions with safe local fallback.
- Doctor/dashboard now expose policy engine and policy-attack sample health signals.
- Governance report now includes policy attack sample status in layered health.
- CI workflow now enforces mypy, ruff, pytest coverage, and doctor smoke checks.

### Security
- Closed backend contract validation and strict policy backend loading paths.
- Governance gate supports policy-attack failure as release blocker.

## Version Strategy

- `0.2.x`: policy-engine contract stabilization and release engineering hardening.
- `0.3.x`: closed strategy enhancement, retrieval/governance refinement, and distribution polish.
- `1.0.0`: production validation milestone after sustained external usage and operational stability.
