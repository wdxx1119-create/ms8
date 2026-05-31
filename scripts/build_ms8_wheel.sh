#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="${ROOT_DIR}/dist"
PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"

echo "[STEP] Clean dist/"
rm -rf "${DIST_DIR}"
mkdir -p "${DIST_DIR}"

echo "[STEP] Build ms8 wheel + sdist"
"${PYTHON_BIN}" -m pip install --upgrade pip build >/dev/null
"${PYTHON_BIN}" -m build

WHEEL="$(ls -1t "${DIST_DIR}"/ms8-*.whl | head -n 1)"
SDIST="$(ls -1t "${DIST_DIR}"/ms8-*.tar.gz | head -n 1)"

echo "[DONE] Build complete"
echo "[INFO] Wheel: ${WHEEL}"
echo "[INFO] Sdist: ${SDIST}"

