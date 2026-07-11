"""MS8 package."""

from __future__ import annotations

import os
import sys
from importlib.metadata import PackageNotFoundError, version

if os.name == "nt":  # pragma: no cover - exercised by Windows wheel smoke
    from . import _compat_fcntl

    # The self-check runner historically imports the POSIX module directly.
    # Register the narrow MS8 compatibility implementation before engine
    # modules are imported so Windows CLI startup follows the same code path.
    sys.modules.setdefault("fcntl", _compat_fcntl)

try:
    __version__ = version("ms8")
except PackageNotFoundError:  # pragma: no cover - source tree fallback
    __version__ = "0.2.15"
