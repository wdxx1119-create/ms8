# MS8 Release Security Guide

## Goal

Use API token publishing without writing credentials into repository files, and verify the exact candidate artifacts before any manual upload.

MS8 does not currently publish to PyPI automatically. The repository's Release Candidate workflow builds and verifies artifacts; publishing remains a separate maintainer action.

## Required environment variables

```bash
export TWINE_USERNAME="__token__"
export TWINE_PASSWORD="<your-pypi-token>"
```

- `TWINE_USERNAME` must be `__token__`
- `TWINE_PASSWORD` is your PyPI/TestPyPI API token

## Verified candidate artifacts

For each candidate commit, `.github/workflows/release-candidate.yml` produces an artifact bundle containing:

- the wheel
- the source distribution
- `ms8-<version>.audit.json`, the strict machine-readable vulnerability audit of the installed wheel environment
- `ms8-<version>.cdx.json`, a CycloneDX JSON SBOM generated from that same clean environment
- `SHA256SUMS`, covering the wheel, source distribution, audit report, and SBOM whenever those evidence files were generated

The workflow also:

- validates package metadata and version-derived filenames
- creates separate clean wheel and source-distribution environments
- refreshes `pip` and `setuptools` in those environments before installation so stale virtual-environment bootstrap tooling is not mistaken for a candidate dependency risk
- runs `pip check`
- verifies packaged MCP resources
- validates that the audit reports no known vulnerabilities
- validates that the SBOM identifies the expected MS8 version
- creates checksums for available package and security evidence before the final decision
- uploads available audit, SBOM, and checksum diagnostics before enforcing the final blocking gate

Generation, checksumming, upload, validation, and enforcement are separate on purpose. A failed audit or malformed SBOM must leave enough machine-readable evidence to diagnose what blocked the candidate.

The reports and checksums improve auditability, but they are not a cryptographic signature or provenance attestation. A maintainer must still confirm that the downloaded bundle belongs to the reviewed commit shown in the workflow summary.

## Dependency security gate

`.github/workflows/dependency-audit.yml` installs MS8 into a dedicated target environment and runs `pip-audit` from a separate tool environment. This keeps the auditor and its own dependencies out of the target inventory. The workflow refreshes the target environment's packaging tools before installing MS8 so the report represents the maintained candidate environment rather than an obsolete bootstrap package bundled by a runner image.

The gate emits:

- `pip-audit.json`
- `ms8-dependencies.cdx.json`

Both files are uploaded before the final enforcement step. A known vulnerability, incomplete strict audit, missing report, or failed SBOM generation makes the workflow fail.

## Workflow supply-chain controls

Third-party GitHub Actions are referenced by immutable full commit SHAs. Version comments remain beside those SHAs so Dependabot can propose reviewed upgrades without silently following a mutable tag.

Workflow permissions remain least-privilege and read-only unless a job has a documented need for additional access.

## Safe flow

1. Run release gate:

```bash
bash scripts/release_checklist.sh
```

2. Confirm the Release Candidate workflow succeeded for the exact commit being released.

3. Download the verified artifact bundle and check `SHA256SUMS` before using the files.

4. Dry run uploads:

```bash
bash scripts/publish_testpypi.sh --dry-run
bash scripts/publish_pypi.sh --dry-run
```

5. Upload to TestPyPI:

```bash
bash scripts/publish_testpypi.sh
```

6. Install from TestPyPI in a clean venv and verify `ms8 doctor`.

7. Upload to PyPI:

```bash
bash scripts/publish_pypi.sh
```

## Security rules

- Never commit tokens in source files, shell history snippets, or docs.
- Never store plaintext credentials in project-local files.
- Prefer short-lived tokens and scope them minimally.
- Rotate a token immediately after any accidental exposure.
- Do not publish artifacts built outside the reviewed candidate workflow without repeating equivalent verification.
- Treat audit reports, SBOMs, and checksums as evidence, not as authorization to publish.

## Emergency rotation checklist

If a token is exposed:

1. Revoke the token in PyPI/TestPyPI account settings.
2. Create a new token with minimal scope.
3. Update the local environment variable only.
4. Re-run the release with the new token.

Operational checklist file:

- `scripts/revoke_checklist.md`

Credential cleanup helper for the current shell:

```bash
source scripts/clear_release_env.sh
```
