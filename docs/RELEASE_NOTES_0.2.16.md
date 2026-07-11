# MS8 0.2.16 Release Candidate Notes

## Release intent

0.2.16 is a reliability and distribution-hardening release. It does not change MS8's product boundary: MS8 remains a local-first memory and governance engine rather than an AI assistant or task orchestrator.

## Major changes

### Verified recovery foundation

- Adds `ms8-recovery` for full-runtime backup creation and verification.
- Adds restore planning and confirmation-gated restore application.
- Uses SQLite Backup API snapshots for active databases.
- Adds checksums, path-traversal rejection, undeclared-file rejection, pre-restore backups, atomic replacement, and audit logs.
- Adds runtime-format versions and a forward-only migration registry.

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

### Supply-chain evidence

- Preserves wheel, sdist, CycloneDX SBOM, audit log, and SHA-256 evidence.
- Adds GitHub build provenance attestations for wheel and sdist.
- Adds an SBOM attestation bound to the wheel.
- Keeps PyPI publication manual for this release; no automated upload workflow is enabled.

## Compatibility

- Python support remains 3.10–3.13.
- `pip install ms8` remains the baseline installation command.
- Existing users of `ms8[absorb-ocr]` remain supported.
- Existing `ms8 backup` behavior remains available; the new complete recovery flow is exposed through `ms8-recovery`.
- No runtime migration is performed automatically on ordinary startup.

## Candidate acceptance checklist

- [ ] Version metadata, README badge, source fallback, filenames, and changelog all say 0.2.16.
- [ ] CI succeeds on Python 3.10–3.13.
- [ ] Core, llm, absorb, ocr, policy, and full installation profiles pass clean-room verification.
- [ ] Windows wheel smoke succeeds under Unicode and space-containing paths.
- [ ] Ubuntu and macOS isolated release tests succeed.
- [ ] Release candidate wheel and sdist install cleanly.
- [ ] Core candidate environment does not contain Ollama.
- [ ] Installed-wheel strict runtime dependency audit succeeds.
- [ ] CycloneDX SBOM validates and contains MS8 0.2.16 as the root component.
- [ ] SHA-256 evidence is generated.
- [ ] Build provenance and wheel SBOM attestations succeed.
- [ ] Final candidate commit is used to create the release tag and artifacts.
- [ ] PyPI upload remains a separate maintainer action.
