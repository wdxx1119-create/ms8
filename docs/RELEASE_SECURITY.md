# MS8 Release Security Guide

## Goal

Use API token publishing without writing credentials into repository files.

## Required environment variables

```bash
export TWINE_USERNAME="__token__"
export TWINE_PASSWORD="pypi-***"
```

- `TWINE_USERNAME` must be `__token__`
- `TWINE_PASSWORD` is your PyPI/TestPyPI API token

## Safe flow

1. Run release gate:

```bash
bash scripts/release_checklist.sh
```

2. Dry run uploads:

```bash
bash scripts/publish_testpypi.sh --dry-run
bash scripts/publish_pypi.sh --dry-run
```

3. Upload to TestPyPI:

```bash
bash scripts/publish_testpypi.sh
```

4. Install from TestPyPI in clean venv and verify `ms8 doctor`.

5. Upload to PyPI:

```bash
bash scripts/publish_pypi.sh
```

## Security rules

- Never commit tokens in source files, shell history snippets, or docs.
- Never store plaintext credentials in project-local files.
- Prefer short-lived tokens and scope them minimally.
- Rotate token immediately after any accidental exposure.

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
