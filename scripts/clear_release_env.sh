#!/usr/bin/env bash
set -euo pipefail

# Clear twine/pypi-related env vars from current shell process.
# Usage:
#   source scripts/clear_release_env.sh
#   # or
#   . scripts/clear_release_env.sh
#
# Note: running with `bash scripts/clear_release_env.sh` won't affect parent shell env.

unset TWINE_USERNAME || true
unset TWINE_PASSWORD || true
unset TEST_PYPI_API_TOKEN || true
unset TESTPYPI_API_TOKEN || true
unset PYPI_API_TOKEN || true
unset PYPI_TOKEN || true

echo "[DONE] Release credential env vars cleared in current shell."
