# MS8 0.2.11

## Highlights

- Added `absorb` as a first-class local content ingestion module.
- Improved `agent-native` install/check/report flows for structured output.
- Unified more of the retrieval and write-path governance behavior.
- Hardened privacy and governance checks across repository save paths.
- Reduced shadow/security user-facing noise and improved health reporting.

## User-Facing Improvements

- `ms8 absorb` now supports:
  - authorized local directory ingestion
  - local parsing and indexing
  - review / quarantine / rollback flows
  - autosubmit with governance safeguards
- `ms8 agent run install --profile DEFAULT_SAFE` remains the recommended AI-assisted onboarding path.
- README and PyPI package description now align on:
  - Absorb support
  - agent-assisted install
  - 11+ AI tool integrations
  - 37 self-repair policies

## Stability and Governance

- Added stronger admission checks in repository save paths.
- Improved privacy detection, including encrypted private key handling.
- Added events log rotation for absorb.
- Improved doctor / governance / self-check consistency.
- Reduced noisy shadow/security terminal output.

## Packaging

- Verified clean wheel and sdist artifacts.
- Confirmed no runtime memory data, local database files, secrets, caches, backups, or local absolute paths are included in release artifacts.

## Validation

- Release artifacts built successfully:
  - `ms8-0.2.11.tar.gz`
  - `ms8-0.2.11-py3-none-any.whl`
- Targeted release regression suite passed locally.
