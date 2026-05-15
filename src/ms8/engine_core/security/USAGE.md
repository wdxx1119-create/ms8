# Local Security Usage

This is an optional local-at-rest encryption layer for openclaw-memory.

The encryption implementation is organized under:

- `memory/security/encryption/` (primary implementation)
- `memory/security/*.py` (backward-compatible shim imports)

Shadow survival layer is organized under:

- `memory/security/shadow/` (audit ledger + seal takeover + recovery replay)

## Commands

Run with:

```bash
PYTHONPATH=python ~/.codex/skills/openclaw-memory/.venv/bin/python -m memory.security.cli security status
```

Supported actions:

1. `security status`
2. `security enable`
3. `security unlock`
4. `security lock`
5. `security disable`
6. `security recover`

Short form also works: `status|enable|unlock|lock|disable|recover`.

Shadow CLI (separate):

```bash
PYTHONPATH=python ~/.codex/skills/openclaw-memory/.venv/bin/python -m memory.security.shadow.shadow_cli status
PYTHONPATH=python ~/.codex/skills/openclaw-memory/.venv/bin/python -m memory.security.shadow.shadow_cli health
PYTHONPATH=python ~/.codex/skills/openclaw-memory/.venv/bin/python -m memory.security.shadow.shadow_cli seal --reason "manual"
PYTHONPATH=python ~/.codex/skills/openclaw-memory/.venv/bin/python -m memory.security.shadow.shadow_cli unseal --reason "manual"
```

## First-time setup

1. Run `security enable` and set a master password.
2. Save the printed recovery key offline.
3. Session is unlocked after enable.

## Runtime behavior

1. Encryption is optional and disabled by default.
2. When enabled and locked, protected files cannot be read/written.
3. Maintenance tasks are blocked in locked state when `require_unlock_for_maintenance=true`.
4. Backups are written as encrypted copies when encryption is enabled and unlocked.

## Recovery

If master password is lost:

1. Run `security recover`
2. Provide recovery key
3. Set a new master password
