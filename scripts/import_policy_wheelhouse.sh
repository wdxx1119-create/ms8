#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
SOURCE_DIR="${1:-${ROOT_DIR}/wheelhouse}"
DIST_DIR="${ROOT_DIR}/dist"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "[ERROR] Missing venv python: ${PYTHON_BIN}" >&2
  exit 1
fi

if [[ ! -d "${SOURCE_DIR}" ]]; then
  echo "[ERROR] Wheelhouse not found: ${SOURCE_DIR}" >&2
  echo "Usage: scripts/import_policy_wheelhouse.sh <wheelhouse-dir>" >&2
  exit 1
fi

POLICY_VERSION="$("${PYTHON_BIN}" - <<'PY'
import tomllib
from pathlib import Path

path = Path("dist_src/ms8_policy_core/pyproject.toml")
if not path.exists():
    raise SystemExit("missing dist_src/ms8_policy_core/pyproject.toml")
print(tomllib.loads(path.read_text(encoding="utf-8"))["project"]["version"])
PY
)"

shopt -s nullglob
WHEELS=("${SOURCE_DIR}"/ms8_policy_core-"${POLICY_VERSION}"-*.whl)
shopt -u nullglob

if (( ${#WHEELS[@]} == 0 )); then
  echo "[ERROR] No ms8_policy_core ${POLICY_VERSION} wheels found in ${SOURCE_DIR}" >&2
  exit 1
fi

echo "[STEP] Validate policy wheels"
"${PYTHON_BIN}" "${ROOT_DIR}/scripts/check_policy_engine_wheel_binary.py" "${WHEELS[@]}"
if [[ "${MS8_ALLOW_INCOMPLETE_POLICY_WHEELHOUSE:-0}" != "1" ]]; then
  "${PYTHON_BIN}" "${ROOT_DIR}/scripts/check_policy_core_wheel_coverage.py" "${WHEELS[@]}"
else
  echo "[WARN] Skipping policy wheelhouse coverage check for local/private testing."
fi

echo "[STEP] Import policy wheels into dist/"
mkdir -p "${DIST_DIR}"
for wheel in "${WHEELS[@]}"; do
  target="${DIST_DIR}/$(basename "${wheel}")"
  if [[ "$(cd "$(dirname "${wheel}")" && pwd)/$(basename "${wheel}")" == "$(cd "${DIST_DIR}" && pwd)/$(basename "${target}")" ]]; then
    echo "[SKIP] Already in dist/: ${target}"
    continue
  fi
  cp -f "${wheel}" "${DIST_DIR}/"
done

echo "[DONE] Imported ${#WHEELS[@]} policy wheel(s):"
printf " - %s\n" "${WHEELS[@]}"
