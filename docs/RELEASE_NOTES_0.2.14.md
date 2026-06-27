# MS8 0.2.14

## Highlights

- Strengthened the local `absorb` surface with the full `project-memory` block instead of the older partial path.
- Brought the broader MCP memory-facing surface onto the clean macOS tree so daily agent workflows can rely on richer memory context.
- Tightened runtime-health interpretation around `doctor`, `watch`, `service`, and related follow-up actions so the enhanced surfaces are more usable in day-to-day operation.

## Absorb and Project Memory

- Added the coherent `project-memory` workflow: scan, index, build, submit, watch, search, health, and service support.
- Expanded local parsing and absorb-side handling so authorized local material can be processed through a stronger end-to-end path.
- Standardized runtime-mode interpretation across background service, foreground watch, and fallback execution paths.

## MCP and Runtime Surface

- Expanded `ask` and MCP-facing status behavior to include the stronger memory surface now present in the project branch.
- Aligned status and health output more closely with current self-check, health-card, and runtime-report structures.
- Improved doctor/watch follow-up guidance so degraded states are easier to interpret and act on.

## Stability and Release Readiness

- Hardened several runtime paths around degraded state reporting, typed exception handling, and cleanup behavior.
- Kept the default adapter template portable so release artifacts do not ship local absolute-path metadata.
- Bound service startup to the active Python environment to avoid background tasks resolving to an unintended `ms8` binary from PATH.

## Validation

- `python3 -m pytest`
- Result: `1259 passed, 5 skipped`
- Coverage: `79.93%`
