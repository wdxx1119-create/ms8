#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
POLICY_SRC_DIR="${ROOT_DIR}/dist_src/ms8_policy_core"
DIST_DIR="${ROOT_DIR}/dist"
VENV_PY="${ROOT_DIR}/.venv/bin/python"
VENV_PIP="${ROOT_DIR}/.venv/bin/pip"
VERIFY_SCRIPT="${ROOT_DIR}/scripts/verify_policy_backend_contract.py"

if [[ ! -x "${VENV_PY}" ]]; then
  echo "[ERROR] Python virtualenv not found at ${VENV_PY}"
  exit 1
fi

if [[ ! -d "${POLICY_SRC_DIR}" ]]; then
  echo "[ERROR] Policy source directory not found: ${POLICY_SRC_DIR}"
  exit 1
fi

echo "[STEP] Build wheel"
cd "${POLICY_SRC_DIR}"
rm -rf build dist target src/ms8_policy_core.egg-info
"${VENV_PY}" -m build --wheel

echo "[STEP] Copy wheel to project dist/"
mkdir -p "${DIST_DIR}"
cp -f "${POLICY_SRC_DIR}"/dist/ms8_policy_core-*.whl "${DIST_DIR}/"

LATEST_WHEEL="$(ls -1t "${DIST_DIR}"/ms8_policy_core-*.whl | head -n 1)"
if [[ -z "${LATEST_WHEEL}" ]]; then
  echo "[ERROR] No wheel generated under ${DIST_DIR}"
  exit 1
fi
echo "[INFO] Wheel: ${LATEST_WHEEL}"

echo "[STEP] Verify binary wheel contents"
"${VENV_PY}" "${ROOT_DIR}/scripts/check_policy_engine_wheel_binary.py" "${LATEST_WHEEL}"

echo "[STEP] Install wheel into project venv"
"${VENV_PIP}" install -U --force-reinstall "${LATEST_WHEEL}"

echo "[STEP] Verify policy backend contract (closed mode)"
MS8_POLICY_BACKEND=closed PYTHONPATH="${ROOT_DIR}/src" "${VENV_PY}" "${VERIFY_SCRIPT}"

echo "[STEP] Run doctor in closed mode"
if MS8_POLICY_BACKEND=closed "${VENV_PY}" -m ms8 doctor >/tmp/ms8_policy_doctor.log 2>&1; then
  rg "policy engine|Overall" /tmp/ms8_policy_doctor.log
else
  MS8_POLICY_BACKEND=closed "${VENV_PY}" -m src.ms8 doctor >/tmp/ms8_policy_doctor.log 2>&1
  rg "policy engine|Overall" /tmp/ms8_policy_doctor.log
fi

echo "[STEP] Clean generated build metadata"
rm -rf "${POLICY_SRC_DIR}/build" "${POLICY_SRC_DIR}/target" "${POLICY_SRC_DIR}/src/ms8_policy_core.egg-info"

echo
echo "[DONE] Policy engine wheel build/install/verify complete."
