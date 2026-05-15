# Branch Protection Template (main/master)

Use this template in GitHub repository settings:

- Settings → Branches → Add branch protection rule
- Branch name pattern: `main` (or `master`)

## Required settings

1. Require a pull request before merging
- Require approvals: `1` (recommended `2` for production-critical repos)
- Dismiss stale pull request approvals when new commits are pushed: `ON`
- Require review from Code Owners: `ON`

2. Require status checks to pass before merging
- Require branches to be up to date before merging: `ON`
- Required checks:
  - `lint-and-test (3.10)`
  - `lint-and-test (3.11)`
  - `lint-and-test (3.12)`
  - `package-smoke`

3. Restrict who can push to matching branches
- Enable restriction and allow only maintainers/release bots.

4. Do not allow bypassing the above settings
- Keep bypass disabled for normal users.

## Recommended settings

- Require conversation resolution before merging: `ON`
- Require signed commits: `ON` (if team already uses signing)
- Include administrators: `ON` (for strict governance)
- Allow force pushes: `OFF`
- Allow deletions: `OFF`

## Tag release policy

For `v*` tags used by `.github/workflows/release.yml`:

- Only maintainers should create release tags.
- Keep `PYPI_API_TOKEN` as an Actions secret if PyPI publish is enabled.
- Prefer release from reviewed/merged commit on protected branch.

## Operational checklist

Before enabling strict mode:

1. Confirm CI workflow names exactly match required checks.
2. Confirm CODEOWNERS is valid and all owners have repo access.
3. Run one test PR to verify checks + approval gate works.
4. Enable release workflow only after CI stability is confirmed.
