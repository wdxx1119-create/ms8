# Security Policy

## Supported Versions

Security fixes are provided for:

- Latest `0.2.x` release line
- `main` / `master` development branch

Older branches are best-effort only.

## Reporting a Vulnerability

If you find a security issue, please avoid opening a public issue first.

Preferred channels:

1. GitHub Security Advisory (private report)
2. Direct maintainer contact

Please include:

- Affected version / commit
- Impact scope (confidentiality, integrity, availability)
- Reproduction steps
- Proof of concept (minimal)
- Suggested mitigation (if available)

We will acknowledge receipt as soon as possible and provide follow-up status during triage.

## Severity and Response Targets

- Critical: initial triage within 24 hours
- High: initial triage within 72 hours
- Medium/Low: initial triage within 7 days

Patch timelines depend on exploitability and ecosystem impact.

## Secret and Token Exposure Response

If a secret is leaked (e.g., GitHub PAT, API key):

1. Revoke exposed credentials immediately.
2. Rotate the credential and dependent integrations.
3. Invalidate cached artifacts that may contain leaked values.
4. Review CI logs, workflow artifacts, and runtime logs for accidental disclosure.
5. Add a postmortem note and mitigation checklist to prevent recurrence.

## Release Security Checklist

Before release tags (`v*`):

1. CI green (`ruff`, `pytest`, package smoke).
2. Branch protection and CODEOWNERS checks enforced.
3. No plaintext secrets in repo/workflows/runtime snapshots.
4. Review `scripts/install_ms8.sh` changes for command safety.
5. Confirm fallback/degraded paths do not bypass governance gates.

## Scope Notes for MS8

Security-relevant areas include:

- `src/ms8/engine_core/security/`
- `src/ms8/connect/` (MCP/connect handshake)
- governance write path and record policy
- installation and release workflows (`.github/workflows/`)

## Disclosure Policy

We follow coordinated disclosure:

- Fix prepared and validated first
- Public details shared after mitigation is available
