"""Backward-compatible import shim.

Encryption implementation now lives in `memory.security.encryption`.
"""

from .encryption.crypto_manager import *  # noqa: F401,F403

