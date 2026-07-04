# MS8 0.2.15

## Highlights

- Restores the packaged `ms8 absorb project-memory ...` command surface.
- Keeps fresh-runtime validation-suite state from incorrectly blocking Windows release checks.
- Tightens source distribution boundaries by excluding test fixtures from release artifacts.

## Validation

- GitHub Actions release validation passed for Python 3.10, 3.11, 3.12, and 3.13.
- Windows LAN smoke validation covered version, doctor, watch, connect status, and connect verify.
- Release artifact inspection confirmed no runtime data, local databases, logs, experiments, backups, or test fixtures are included.
