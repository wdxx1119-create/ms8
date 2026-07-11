# MS8 0.2.16 Release Notes

## Release intent

MS8 0.2.16 is a reliability, governance, recovery, and distribution-hardening release. It does not change MS8's product boundary: MS8 remains a local-first memory and governance engine rather than an AI assistant or task orchestrator.

## Major changes

### Verified recovery foundation

- Adds `ms8-recovery` for full-runtime backup creation and verification.
- Adds restore planning and confirmation-gated restore application.
- Uses SQLite Backup API snapshots for active databases.
- Adds checksums, path-traversal rejection, undeclared-file rejection, pre-restore backups, atomic replacement, and audit logs.
- Adds runtime-format versions and a forward-only migration registry.
- Adds deterministic fault-injection coverage for corrupted data, interrupted restoration, and safe retry.

### Verifiable memory provenance and action governance

- Adds backward-compatible provenance metadata for canonical memory records.
- Records source identity, content digest, timestamps, transformation lineage, verification state, and confidence.
- Adds an advisory MCP `pre_action_check` interface with structured reason codes.
- Requires explicit supporting memory IDs, verified user-explicit authority, exact action-scope matching, uniform evidence eligibility, and explicit human confirmation.
- Keeps execution outside MS8: action checks always report a governance decision and never perform the external action.

### Installation profile cleanup

- Removes Ollama from the mandatory core dependency set.
- Adds explicit `llm`, `absorb`, `ocr`, `policy`, and `full` profiles.
- Retains `absorb-ocr` as a compatibility alias.
- Adds clean-room CI verification for every supported profile.

### CI and candidate validation

- Keeps the Python 3.10–3.13 matrix in normal CI.
- Runs candidate validation only for `candidate/**`, `rc-*`, or explicit manual dispatch.
- Reduces repeated macOS matrix execution to one macOS boundary job.
- Builds and audits release artifacts on Ubuntu.
- Adds a weekly macOS/Windows boundary matrix for Python 3.10 and 3.13.
- Enforces an 80% line-coverage baseline.

### Supply-chain evidence

- Preserves wheel, sdist, CycloneDX SBOM, audit log, and SHA-256 evidence.
- Adds GitHub build provenance attestations for wheel and sdist.
- Adds an SBOM attestation bound to the wheel.
- Uses a strict installed-wheel runtime dependency audit.
- Keeps PyPI publication manual for this release; no automated upload workflow is enabled.

## Compatibility

- Python support remains 3.10–3.13.
- `pip install ms8` remains the baseline installation command.
- Existing users of `ms8[absorb-ocr]` remain supported.
- Existing `ms8 backup` behavior remains available; the complete recovery flow is exposed through `ms8-recovery`.
- No runtime migration is performed automatically on ordinary startup.
- Unnamed MCP submissions retain the legacy `mcp:submit` and `mcp:batch_submit` source identifiers; named clients receive `mcp:<client>` provenance.

## Release acceptance checklist

- [x] Version metadata, README badge, source fallback, filenames, and changelog say 0.2.16.
- [x] CI succeeds on Python 3.10–3.13.
- [x] Core, llm, absorb, ocr, policy, and full installation profiles pass clean-room verification.
- [x] Windows wheel smoke succeeds under Unicode and space-containing paths.
- [x] Ubuntu and macOS isolated release tests succeed.
- [x] Release candidate wheel and sdist install cleanly.
- [x] Core candidate environment does not contain Ollama.
- [x] Installed-wheel strict runtime dependency audit succeeds.
- [x] CycloneDX SBOM validates and contains MS8 0.2.16 as the root component.
- [x] SHA-256 evidence is generated.
- [x] Build provenance and wheel SBOM attestations succeed.
- [ ] Create the final `v0.2.16` tag and GitHub Release from the exact post-merge commit.
- [ ] Upload the exact attested wheel and sdist to PyPI as a separate maintainer action.
