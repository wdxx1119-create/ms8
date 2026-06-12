# MS8 0.2.13

## Highlights

- Realigned the public release path around the versions and workflows that are actually validated end to end.
- Kept `absorb`, `agent-native`, `doctor`, `connect`, and the governed memory runtime as the main supported surface.
- Moved the closed policy backend to an optional extra so default installation stays simple and cross-platform.
- Tightened the open fallback behavior so MS8 still applies meaningful guardrails when the closed backend is unavailable.

## Stability and Packaging

- `requires-python` is now constrained to `>=3.10,<3.14`, matching the validated support window.
- Public CI and release engineering no longer depend on private policy-core wheel pipelines.
- No-isolation builds were stabilized by ensuring `wheel` and modern `setuptools` are present in the build path.
- Release isolation and artifact validation were strengthened for clean wheel/sdist output.

## Runtime and Governance

- Open-policy fallback admission now better handles short valid memories instead of over-rejecting them.
- TOML parsing support was fixed for Python 3.10 environments.
- Open fallback recovery and admission behavior were hardened so degraded mode is still governed rather than effectively open.

## Packaging Boundaries

- Experimental design work such as `MS8 project_memory v0 Prototype Design` is not part of this release.
- LAN experiment materials are kept in the repository for ongoing development but excluded from published release artifacts.
- Release artifacts were re-checked to confirm they do not include runtime memory data, local databases, secrets, caches, or local absolute paths.

## Validation

- `python -m build --sdist --wheel --no-isolation` passed locally.
- `bash scripts/check_release_artifacts.sh` passed locally.
- Wheel and sdist contents were checked again after the latest documentation and packaging updates.
