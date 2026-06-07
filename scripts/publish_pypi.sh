#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "[ERROR] Missing venv python: ${PYTHON_BIN}" >&2
  exit 1
fi

MS8_VERSION="$("${PYTHON_BIN}" - <<'PY'
import tomllib
from pathlib import Path
print(tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]["version"])
PY
)"

if [[ "${TWINE_USERNAME:-}" != "__token__" ]]; then
  echo "[ERROR] TWINE_USERNAME must be '__token__'." >&2
  exit 1
fi

if [[ -z "${TWINE_PASSWORD:-}" ]]; then
  echo "[ERROR] TWINE_PASSWORD is required (PyPI API token)." >&2
  exit 1
fi

if [[ ! -d "${ROOT_DIR}/dist" ]]; then
  echo "[ERROR] dist/ not found. Run build first." >&2
  exit 1
fi

ARTIFACTS=()

POLICY_VERSION="$("${PYTHON_BIN}" - <<'PY'
import tomllib
from pathlib import Path
path = Path("dist_src/ms8_policy_core/pyproject.toml")
if path.exists():
    print(tomllib.loads(path.read_text(encoding="utf-8"))["project"]["version"])
PY
)"
if [[ -n "${POLICY_VERSION}" ]]; then
  shopt -s nullglob
  POLICY_WHEELS=("${ROOT_DIR}"/dist/ms8_policy_core-"${POLICY_VERSION}"-*.whl)
  shopt -u nullglob
  if (( ${#POLICY_WHEELS[@]} == 0 )); then
    echo "[ERROR] Missing required ms8-policy-core ${POLICY_VERSION} wheel(s)." >&2
    echo "[ERROR] Build/import policy core wheels before publishing ms8." >&2
    exit 1
  fi
  "${PYTHON_BIN}" "${ROOT_DIR}/scripts/check_policy_engine_wheel_binary.py" "${POLICY_WHEELS[@]}"
  if [[ "${MS8_ALLOW_INCOMPLETE_POLICY_WHEELHOUSE:-0}" != "1" ]]; then
    "${PYTHON_BIN}" "${ROOT_DIR}/scripts/check_policy_core_wheel_coverage.py" "${POLICY_WHEELS[@]}"
  else
    echo "[WARN] Skipping policy wheelhouse coverage check for local/private testing."
  fi
  ARTIFACTS+=("${POLICY_WHEELS[@]}")
fi

ARTIFACTS+=(
  "${ROOT_DIR}/dist/ms8-${MS8_VERSION}-py3-none-any.whl"
  "${ROOT_DIR}/dist/ms8-${MS8_VERSION}.tar.gz"
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

echo "[STEP] Upload to PyPI"
"${PYTHON_BIN}" -m twine upload \
  --skip-existing \
  "${ARTIFACTS[@]}"

echo "[DONE] Uploaded to PyPI."
