# MS8 Release Security Guide

## Goal

Verify the exact candidate artifacts before any manual upload, without writing credentials into repository files.

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
- `ms8-<version>.cdx.json`, a CycloneDX JSON SBOM generated from a clean environment containing the installed wheel
- `SHA256SUMS`, covering the wheel, source distribution, and SBOM

The workflow also:

- validates package metadata and version-derived filenames
- installs the wheel and source distribution in separate clean virtual environments
- runs `pip check`
- verifies packaged MCP resources
- performs a strict vulnerability audit of the installed wheel environment while generating the SBOM

The SBOM and checksums improve auditability, but they are not a cryptographic signature or provenance attestation. A maintainer must still confirm that the downloaded bundle belongs to the reviewed commit shown in the workflow summary.

## Dependency security gate

`.github/workflows/dependency-audit.yml` installs MS8 into a dedicated target environment and runs `pip-audit` from a separate tool environment. This keeps the auditor and its own dependencies out of the target dependency inventory.

The gate emits:

- `pip-audit.json`
- `ms8-dependencies.cdx.json`

Both artifacts are uploaded even when the audit fails. A known vulnerability, incomplete strict audit, missing report, or failed SBOM generation makes the workflow fail.

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
- Scope tokens minimally and rotate them after any suspected exposure.
- Do not publish artifacts built outside the reviewed candidate workflow without repeating equivalent verification.
- Treat SBOMs and checksums as evidence, not as authorization to publish.

## Emergency rotation checklist

If a token is exposed:

1. Revoke token in PyPI/TestPyPI account settings.
2. Create a new token with minimal scope.
3. Update local environment variable only.
4. Re-run release with the new token.

Operational checklist file:

- `scripts/revoke_checklist.md`

Credential cleanup helper (current shell):

```bash
source scripts/clear_release_env.sh
```
