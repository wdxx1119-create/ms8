# Trusted Publishing Preparation

MS8 0.2.16 does **not** enable automatic PyPI publication. This document records the future migration path from long-lived API tokens to PyPI Trusted Publishing while preserving explicit maintainer approval.

## Current state

- Release candidate artifacts are built and verified by GitHub Actions.
- Wheel, sdist, CycloneDX SBOM, audit log, SHA-256 checksums, build provenance, and SBOM attestations are generated as evidence.
- PyPI/TestPyPI upload remains a separate maintainer action.
- No repository workflow currently has permission or logic to upload to PyPI.

## Preconditions before enabling Trusted Publishing

1. Finish at least one complete candidate cycle with artifact attestations enabled.
2. Confirm the final tag and GitHub Release process uses the exact reviewed candidate commit.
3. Create a protected GitHub Environment such as `pypi`.
4. Require human approval for that environment.
5. Restrict the environment to release tags matching the adopted version policy.
6. Confirm the package owner account and recovery options on PyPI.
7. Remove or rotate repository-scoped upload tokens after OIDC publishing has been proven.

## Future PyPI project configuration

When the maintainer decides to enable Trusted Publishing, register a pending publisher for:

```text
PyPI project: ms8
GitHub owner: wdxx1119-create
GitHub repository: ms8
Workflow filename: publish.yml
Environment name: pypi
```

The workflow filename and environment must exactly match the eventual GitHub Actions workflow.

TestPyPI should be configured separately before production PyPI.

## Future workflow security contract

The eventual publishing workflow must:

- trigger only from an explicit release/tag event, not ordinary pushes or pull requests;
- use a protected `pypi` environment with manual approval;
- request only `contents: read` and `id-token: write` permissions;
- download or rebuild artifacts from the exact reviewed release commit;
- verify SHA-256 checksums and GitHub attestations before upload;
- use PyPI's OIDC exchange rather than a stored API token;
- reject version/tag mismatches;
- never publish from fork pull requests;
- retain a dry-run or TestPyPI path before production.

## Manual activation boundary

Creating a Trusted Publisher and protected environment changes external account and repository settings. Those settings cannot be represented safely by source files alone and must be performed deliberately by the repository maintainer.

Do not add `publish.yml` until the maintainer explicitly authorizes automated publishing. Until then, `docs/RELEASE_SECURITY.md` remains the active manual publication procedure.
