#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="${ROOT_DIR}/dist"
VENV_PY="${ROOT_DIR}/.venv/bin/python"

echo "[STEP] Build + install + verify closed policy backend"
bash "${ROOT_DIR}/scripts/build_policy_engine_wheel.sh"

echo "[STEP] Policy-focused regression"
"${VENV_PY}" -m pytest \
  "${ROOT_DIR}/tests/test_policy_engine_loader.py" \
  "${ROOT_DIR}/tests/test_policy_engine_iface_contract.py" \
  "${ROOT_DIR}/tests/test_policy_engine_phase2_hooks.py" \
  "${ROOT_DIR}/tests/test_policy_engine_phase4_hooks.py" \
  "${ROOT_DIR}/tests/test_policy_engine_regressions.py" \
  "${ROOT_DIR}/tests/test_policy_engine_closed_adversarial.py" \
  "${ROOT_DIR}/tests/test_policy_engine_attack_samples.py" \
  "${ROOT_DIR}/tests/test_runtime_policy_attack_report.py" \
  "${ROOT_DIR}/tests/test_shadow_policy_engine_hook.py" \
  --no-cov -q

echo "[STEP] Policy attack sample gate"
PYTHONPATH="${ROOT_DIR}/src" \
MS8_POLICY_BACKEND=closed \
MS8_HOME="${ROOT_DIR}/.tmp_runtime_probe_policy_attack" \
"${VENV_PY}" "${ROOT_DIR}/scripts/policy_attack_sample_report.py"

LATEST_WHEEL="$(ls -1t "${DIST_DIR}"/ms8_policy_engine-*.whl | head -n 1)"
echo "[DONE] Local policy-engine release validation passed."
echo "[INFO] Wheel ready: ${LATEST_WHEEL}"
