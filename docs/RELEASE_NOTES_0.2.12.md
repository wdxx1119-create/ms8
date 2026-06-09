# MS8 0.2.12

## Highlights

- Realigned the published Python support window to the versions that are actually validated end to end: `3.10` to `3.13`.
- Cleaned the main repository's GitHub Actions surface by removing private policy-core build/release workflows from the public MS8 release path.
- Fixed a CI-blocking mypy regression and cleaned duplicate packaging artifacts before release.

## Stability and Packaging

- `CI` now targets `3.10`, `3.11`, `3.12`, and `3.13`.
- The main MS8 repository no longer advertises policy-core wheel automation that depends on private source not stored in this repo.
- Release artifacts were rebuilt after removing duplicate `* 2` packaging leftovers and Python cache noise.
- `ms8-policy-core` remains an optional closed-backend enhancement so the default `pip install ms8` path stays cross-platform.

## Validation

- `mypy src/ms8` passed.
- `ruff check src/ms8` passed.
- Targeted regression tests passed locally.
- `ms8 doctor` remained `Overall: healthy`.
