"""Backward-compatible import shim.

Encryption implementation now lives in `memory.security.encryption`.
"""

from .encryption.file_crypto import *  # noqa: F401,F403
