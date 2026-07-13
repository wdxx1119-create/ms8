"""MS8 package."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("ms8")
except PackageNotFoundError:  # pragma: no cover - source tree fallback
    __version__ = "0.2.17"
