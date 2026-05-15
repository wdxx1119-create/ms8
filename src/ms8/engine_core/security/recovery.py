"""Backward-compatible import shim.

Encryption implementation now lives in `memory.security.encryption`.
"""

from .encryption.recovery import *  # noqa: F401,F403
