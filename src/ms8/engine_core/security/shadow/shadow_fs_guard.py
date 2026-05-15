from __future__ import annotations

import os
import stat
from pathlib import Path


def set_mutable(path: Path, *, enabled: bool = True) -> None:
    if not enabled:
        return
    try:
        if hasattr(os, "chflags"):
            os.chflags(path, 0)
    except OSError:
        return


def set_immutable(path: Path, *, enabled: bool = True) -> None:
    if not enabled:
        return
    try:
        if hasattr(os, "chflags"):
            os.chflags(path, stat.UF_IMMUTABLE)
    except OSError:
        return
