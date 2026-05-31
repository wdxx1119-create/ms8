#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
MS8_HOME_DIR="${MS8_HOME:-/tmp/ms8-release}"

cd "${ROOT_DIR}"

echo "[STEP] mypy src/ms8"
"${PYTHON_BIN}" -m mypy src/ms8

echo "[STEP] ruff check src/ms8"
"${PYTHON_BIN}" -m ruff check src/ms8

echo "[STEP] pytest with coverage gate"
rm -f "${ROOT_DIR}/.coverage" "${ROOT_DIR}"/.coverage.*
COVERAGE_FILE="${MS8_HOME_DIR}/.coverage.release" \
  "${PYTHON_BIN}" -m pytest tests/ --cov=src/ms8 --cov-fail-under=75 -q

echo "[STEP] doctor smoke"
PYTHONPATH=src \
MS8_HOME="${MS8_HOME_DIR}" \
OPENCLAW_MEMORY_SESSION_INGEST_ENABLED="0" \
MS8_DOCTOR_ALLOW_DEGRADED="1" \
"${PYTHON_BIN}" -m src.ms8 doctor

echo "[STEP] build artifacts"
"${PYTHON_BIN}" -m build

echo "[DONE] release checklist passed"
