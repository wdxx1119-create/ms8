#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "[ERROR] Missing venv python: ${PYTHON_BIN}" >&2
  exit 1
fi

if [[ "${TWINE_USERNAME:-}" != "__token__" ]]; then
  echo "[ERROR] TWINE_USERNAME must be '__token__'." >&2
  exit 1
fi

if [[ -z "${TWINE_PASSWORD:-}" ]]; then
  echo "[ERROR] TWINE_PASSWORD is required (TestPyPI API token)." >&2
  exit 1
fi

if [[ ! -d "${ROOT_DIR}/dist" ]]; then
  echo "[ERROR] dist/ not found. Run build first." >&2
  exit 1
fi

ARTIFACTS=(
  "${ROOT_DIR}/dist/ms8-0.2.0-py3-none-any.whl"
  "${ROOT_DIR}/dist/ms8-0.2.0.tar.gz"
  "${ROOT_DIR}/dist/ms8_policy_engine-0.1.1-py3-none-any.whl"
)

for f in "${ARTIFACTS[@]}"; do
  if [[ ! -f "${f}" ]]; then
    echo "[ERROR] Missing artifact: ${f}" >&2
    exit 1
  fi
done

if [[ "${1:-}" == "--dry-run" ]]; then
  echo "[DRY-RUN] Would upload:"
  printf " - %s\n" "${ARTIFACTS[@]}"
  exit 0
fi

echo "[STEP] Upload to TestPyPI"
"${PYTHON_BIN}" -m twine upload \
  --repository-url https://test.pypi.org/legacy/ \
  --skip-existing \
  "${ARTIFACTS[@]}"

echo "[DONE] Uploaded to TestPyPI."
