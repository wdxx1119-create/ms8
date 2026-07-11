# MS8 Release Security Guide

## Goal

Verify the exact candidate artifacts and their GitHub provenance before any manual upload, without writing credentials into repository files.

MS8 does not currently publish to PyPI automatically. The Release Candidate workflow builds, audits, attests, and retains artifacts; publishing remains a separate maintainer action.

The candidate workflow runs only for `candidate/**` branch pushes or explicit manual dispatch. Ordinary pull requests use the normal CI workflow and do not consume the full candidate path.

## Current publication credentials

Until Trusted Publishing is explicitly enabled, manual upload scripts use environment variables:

```bash
export TWINE_USERNAME="__token__"
export TWINE_PASSWORD="<your-pypi-token>"
```

- `TWINE_USERNAME` must be `__token__`.
- `TWINE_PASSWORD` is a PyPI or TestPyPI API token.
- Never place either value in repository files, command examples committed with real values, workflow YAML, or project-local environment files.

The planned OIDC migration and external configuration boundary are documented in [TRUSTED_PUBLISHING_SETUP.md](TRUSTED_PUBLISHING_SETUP.md). No automatic PyPI upload workflow is enabled for 0.2.16.

## Verified candidate artifacts

For each candidate commit, `.github/workflows/release-candidate.yml` produces an evidence bundle containing:

- the wheel;
- the source distribution;
- `ms8-<version>.cdx.json`, a CycloneDX JSON SBOM generated from the clean core wheel environment;
- `SHA256SUMS`, covering wheel, source distribution, and SBOM;
- `wheel-audit.log`, preserving strict installed-wheel audit output.

The workflow also:

- validates package metadata and version-derived filenames;
- verifies that the core installation does not accidentally install Ollama;
- installs wheel and source distribution in separate clean virtual environments;
- runs `pip check`;
- verifies packaged MCP and recovery entry points;
- performs a strict vulnerability audit of the installed core wheel environment;
- creates GitHub build provenance attestations for wheel and source distribution;
- creates an SBOM attestation binding the CycloneDX document to the wheel;
- blocks the candidate if audit, SBOM validation, checksum generation, provenance, or SBOM attestation fails.

The attestation workflow requests only:

```yaml
permissions:
  contents: read
  id-token: write
  attestations: write
```

## Dependency security gate

`.github/workflows/dependency-audit.yml` installs MS8 into a dedicated target environment and runs `pip-audit` from a separate tool environment. This keeps the auditor and its own dependencies out of the target dependency inventory.

The gate emits:

- `pip-audit.json`;
- `ms8-dependencies.cdx.json`.

Both artifacts are uploaded even when the audit fails. A known vulnerability, incomplete strict audit, missing report, or failed SBOM generation makes the workflow fail.

## Safe candidate flow

1. Run the local release gate:

```bash
python scripts/release_checklist.py
```

macOS/Linux may use the compatibility wrapper:

```bash
bash scripts/release_checklist.sh
```

2. Push the exact candidate commit to a `candidate/**` branch.

3. Confirm normal CI and Release Candidate validation both succeed for that commit.

4. Confirm all installation profiles succeeded:

```text
core
llm
absorb
ocr
policy
full
```

5. Download the candidate evidence bundle and verify `SHA256SUMS`.

6. Verify GitHub artifact provenance for the wheel and source distribution:

```bash
gh attestation verify dist/ms8-<version>-py3-none-any.whl -R wdxx1119-create/ms8
gh attestation verify dist/ms8-<version>.tar.gz -R wdxx1119-create/ms8
```

7. Verify the wheel's SBOM attestation using the same repository identity and retained SBOM evidence.

8. Confirm the candidate commit, artifact names, package metadata, changelog, and intended release tag all use the same version.

9. Dry-run uploads:

```bash
bash scripts/publish_testpypi.sh --dry-run
bash scripts/publish_pypi.sh --dry-run
```

10. Upload to TestPyPI:

```bash
bash scripts/publish_testpypi.sh
```

11. Install from TestPyPI in a clean virtual environment and verify `ms8 doctor`, `ms8-recovery --help`, and the intended installation profile.

12. Upload the already-verified artifacts to PyPI:

```bash
bash scripts/publish_pypi.sh
```

Do not rebuild between candidate verification and upload. Rebuilding creates different artifact bytes and invalidates the reviewed checksums and attestations.

## Security rules

- Never commit tokens in source files, shell history snippets, or documentation containing real values.
- Never store plaintext credentials in project-local files.
- Scope tokens minimally and rotate them after any suspected exposure.
- Do not publish artifacts built outside the reviewed candidate workflow without repeating equivalent verification and attestation.
- Treat checksums as integrity evidence, GitHub attestations as build-identity evidence, and neither as authorization to publish.
- Require explicit maintainer approval before publication.
- Do not add a PyPI publishing workflow until automated publishing is separately authorized.

## Emergency rotation checklist

If a token is exposed:

1. Revoke the token in PyPI or TestPyPI account settings.
2. Create a new token with minimal project scope.
3. Update only the maintainer's secure local environment.
4. Clear exported values from the current shell.
5. Re-run the release verification if the exposed token was used during the candidate process.

Operational checklist file:

- `scripts/revoke_checklist.md`

Credential cleanup helper for the current shell:

```bash
source scripts/clear_release_env.sh
```
