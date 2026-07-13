# MS8 0.2.17 Release Notes

MS8 0.2.17 is a focused patch release candidate for the opt-in Memory Ledger v1 foundation and related macOS/Windows stability fixes.

## Scope

- Adds Memory Ledger v1 append-only lifecycle decisions, temporal queries, rebuildable projections, and explicit CLI/MCP surfaces.
- Keeps Memory Ledger v1 disabled by default.
- Preserves existing CLI, MCP, and legacy runtime behavior.
- Does not include the LAN module.
- Does not switch existing user runtime formats.
- Does not publish PyPI artifacts automatically.

## Validation Matrix

Validated platforms:
- macOS
- Windows

Linux:
- best-effort compatibility
- not part of the 0.2.17 formal validation matrix

## Release Candidate Checklist

- [x] Version metadata, README badge, source fallback, filenames, and changelog say 0.2.17.
- [x] Memory Ledger v1 remains opt-in and disabled by default.
- [x] LAN module files are not added or modified by this candidate.
- [ ] Public CI and final maintainer release-candidate validation have completed.
- [ ] Create the final `v0.2.17` tag and GitHub Release after maintainer approval.
- [ ] Publish PyPI artifacts after maintainer approval.
