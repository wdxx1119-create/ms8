"""Backward-compatible entrypoint shim.

Encryption CLI now lives in `memory.security.encryption.cli`.
"""

from .encryption.cli import *  # noqa: F401,F403

if __name__ == "__main__":
    from .encryption.cli import main
    raise SystemExit(main())
