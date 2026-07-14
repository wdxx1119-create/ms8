# MS8 Release Security Guide

## Goal

Verify the exact candidate artifacts and their GitHub provenance before any manual upload, without writing credentials into repository files.

MS8 does not currently publish to PyPI automatically. The Release Candidate workflow builds, audits, attests, and retains artifacts; publishing remains a separate maintainer action.

The candidate workflow runs only for `candidate/**` or `rc-*` branch pushes, or explicit manual dispatch. Ordinary pull requests and `main` pushes do not consume the full candidate path. The `rc-*` convention exists for repositories whose rulesets reserve or block creation of the `candidate/**` namespace.

## Current publication credentials

Until Trusted Publishing is explicitly enabled, manual upload scripts use environment variables:

```bash
export TWINE_USERNAME="__token__"
export TWINE_PASSWORD="<your-pypi-token>"
```

- `TWINE_USERNAME` must be `__token__`.
- `TWINE_PASSWORD` is a PyPI or TestPyPI API token.
- Never place either value in repository files, command examples committed with real values, workflow YAML, or project-local environment files.

Trusted Publishing is not enabled. No automatic PyPI upload workflow is currently authorized.

## Verified candidate artifacts

For each candidate commit, `.github/workflows/release-candidate.yml` produces an evidence bundle containing:

- the wheel;
- the source distribution;
- `ms8-<version>.cdx.json`, a CycloneDX JSON SBOM generated from the exact installed runtime dependency closure;
- `SHA256SUMS`, covering wheel, source distribution, and SBOM;
- `wheel-audit-requirements.txt`, the exact pinned dependency closure used for audit;
- `wheel-audit.json`, the strict vulnerability report;
- `wheel-audit.log`, preserving audit execution output.

The workflow also:

- validates package metadata and version-derived filenames;
- verifies that the core installation does not accidentally install Ollama;
- installs wheel and source distribution in separate clean virtual environments;
- runs `pip check`;
- verifies packaged MCP and recovery entry points;
- performs a strict vulnerability audit of the installed runtime dependency closure without querying the unpublished candidate root package;
- creates GitHub build provenance attestations for wheel and source distribution;
- creates an SBOM attestation binding the CycloneDX document to the wheel;
- blocks the candidate if audit, SBOM validation, checksum generation, provenance, or SBOM attestation fails;
- publishes `release-candidate/aggregate` as a commit status so successful push-triggered candidates are directly auditable.

## Job-scoped permissions

The workflow default is read-only:

```yaml
permissions:
  contents: read
```

Only the release-artifact job receives OIDC and attestation rights:

```yaml
permissions:
  contents: read
  id-token: write
  attestations: write
```

Only the final aggregate-status job receives commit-status write access:

```yaml
permissions:
  contents: read
  statuses: write
```

Static analysis and macOS runtime-boundary jobs do not receive either permission set.

## Dependency security gate

`.github/workflows/dependency-audit.yml` installs MS8 into a dedicated target environment and runs `pip-audit` from a separate tool environment. The reusable `scripts/audit_installed_environment.py` helper computes the exact transitive runtime dependency closure, writes fully pinned requirements, and audits them with strict `--no-deps` semantics. The unpublished MS8 candidate itself and unrelated environment tooling are not sent to the vulnerability service.

The gate emits:

- `audit-requirements.txt`;
- `pip-audit.json`;
- `ms8-dependencies.cdx.json`;
- `dependency-audit.log`.

All evidence is uploaded even when the audit fails. A known vulnerability, unresolved declared runtime dependency, incomplete strict audit, missing report, or failed SBOM generation makes the workflow fail.

## Safe candidate flow

1. Run the local release gate:

```bash
python scripts/release_checklist.py
```

macOS/Linux may use the compatibility wrapper:

```bash
bash scripts/release_checklist.sh
```

2. Push the exact PR head to a `candidate/**` branch. If repository rules prevent that namespace, use a dedicated `rc-*` branch such as `rc-v0.2.16`.

3. Confirm normal CI succeeds for the PR head.

4. Confirm the pre-merge candidate commit has a successful `release-candidate/aggregate` status before merging.

5. Merge the reviewed PR. If the merge method creates a different commit SHA, as squash, rebase, and ordinary merge usually do, move the candidate branch to the exact final `main` commit that will receive the release tag and run Release Candidate validation again.

6. Treat only the evidence bundle and attestations generated from the exact intended tag target as authoritative. Confirm that commit has a successful `release-candidate/aggregate` status after static quality, macOS runtime tests, artifact audit, SBOM validation, checksum generation, provenance, and SBOM attestation all succeed.

7. Confirm all installation profiles succeeded:

```text
core
llm
absorb
ocr
policy
full
```

8. Download the authoritative candidate evidence bundle and verify `SHA256SUMS`.

9. Verify GitHub artifact provenance for the wheel and source distribution:

```bash
gh attestation verify dist/ms8-<version>-py3-none-any.whl -R wdxx1119-create/ms8
gh attestation verify dist/ms8-<version>.tar.gz -R wdxx1119-create/ms8
```

10. Verify the wheel's SBOM attestation using the same repository identity and retained SBOM evidence.

11. Confirm the candidate commit, artifact names, package metadata, changelog, intended release tag, and tag target commit all use the same version and identity.

12. Dry-run uploads:

```bash
bash scripts/publish_testpypi.sh --dry-run
bash scripts/publish_pypi.sh --dry-run
```

13. Upload to TestPyPI:

```bash
bash scripts/publish_testpypi.sh
```

14. Install from TestPyPI in a clean virtual environment and verify `ms8 doctor`, `ms8-recovery --help`, and the intended installation profile.

15. Upload the already-verified authoritative artifacts to PyPI:

```bash
bash scripts/publish_pypi.sh
```

Do not rebuild between authoritative candidate verification and upload. Rebuilding creates different artifact bytes and invalidates the reviewed checksums and attestations.

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
