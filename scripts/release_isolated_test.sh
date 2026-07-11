#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

MODE="--keep"
if [[ "${1:-}" == "--cleanup" ]]; then
  MODE="--cleanup"
elif [[ "${1:-}" == "--keep" || -z "${1:-}" ]]; then
  MODE="--keep"
else
  echo "Usage: bash scripts/release_isolated_test.sh [--keep|--cleanup]"
  exit 2
fi

echo "[INFO] release isolated test start"
echo "[INFO] project root: $ROOT_DIR"
echo "[INFO] mode: offline/isolation validation (no real ~/.ms8 touched)"

rm -rf dist build ./*.egg-info

if [[ -z "${PY_BIN:-}" ]]; then
  if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
    PY_BIN="$ROOT_DIR/.venv/bin/python"
  else
    PY_BIN="python3"
  fi
fi

if ! "$PY_BIN" -c "import build" >/dev/null 2>&1; then
  "$PY_BIN" -m pip install build
fi

"$PY_BIN" -m build --no-isolation

WHEEL="$(ls dist/*.whl | head -n 1)"
SDIST="$(ls dist/*.tar.gz | head -n 1)"

BASE_TMP="$(mktemp -d "${TMPDIR:-/tmp}/ms8-release-XXXXXX")"
TMP_VENV="$BASE_TMP/venv"
TMP_HOME="$BASE_TMP/home"
TMP_MS8_HOME="$TMP_HOME/.ms8"
TMP_DATA="$TMP_MS8_HOME/data"
TMP_CONFIG="$TMP_MS8_HOME/config"
TMP_LOGS="$TMP_MS8_HOME/logs"
LOG_DIR="$BASE_TMP/logs"
SMOKE_DIR="$BASE_TMP/smoke"
mkdir -p "$TMP_HOME" "$TMP_DATA" "$TMP_CONFIG" "$TMP_LOGS" "$LOG_DIR" "$SMOKE_DIR"

cat >"$SMOKE_DIR/connect_package_resources.py" <<'PY'
from ms8.connect.scripts.common import connect_package_root, load_cfg, read_json

root = connect_package_root()
cfg_path = root / "config" / "mcp_config.yaml"
registry_path = root / "adapter_registry" / "adapters.json"

assert cfg_path.exists(), f"missing packaged MCP config: {cfg_path}"
assert registry_path.exists(), f"missing packaged adapter registry: {registry_path}"

cfg = load_cfg()
assert cfg.get("mcp", {}).get("enabled") is True, cfg

registry = read_json(registry_path)
adapter = registry.get("ms8_default_adapter", {})
assert adapter.get("status") == "active", registry
capabilities = set(adapter.get("capabilities", []))
expected = {"submit", "query", "context", "status", "profile"}
assert expected.issubset(capabilities), capabilities

print("connect package resources ok")
PY

cat >"$SMOKE_DIR/absorb_parser_smoke.py" <<'PY'
import os
from pathlib import Path

from ms8.absorb.parser import parse_document

sample = Path(os.environ["MS8_HOME"]) / "absorb-smoke.txt"
sample.write_text("MS8 absorb smoke document\n", encoding="utf-8")

doc = parse_document(sample)
assert doc.parse_status == "parsed", doc
assert doc.file_type == ".txt", doc
assert "MS8 absorb smoke document" in doc.content_text, doc.content_text
assert len(doc.content_hash) == 64, doc.content_hash

print("absorb parser smoke ok")
PY

cat >"$SMOKE_DIR/ask_records_smoke.py" <<'PY'
from ms8.runtime import ensure_runtime_dirs

paths = ensure_runtime_dirs()
records = paths["memories"]
assert records.exists(), f"missing memory records file: {records}"
text = records.read_text(encoding="utf-8")
assert "release isolated test memory" in text, text[-1000:]

print("ask records smoke ok")
PY

"$PY_BIN" -m venv --system-site-packages "$TMP_VENV"
if [[ "${MS8_RELEASE_INSTALL_NO_DEPS:-0}" == "1" ]]; then
  "$TMP_VENV/bin/python" -m pip install --no-deps "$WHEEL"
else
  "$TMP_VENV/bin/python" -m pip install "$WHEEL"
fi

run_step() {
  local name="$1"
  shift
  local stdout_log="$LOG_DIR/${name}.out.log"
  local stderr_log="$LOG_DIR/${name}.err.log"
  echo "[STEP] $name"
  if HOME="$TMP_HOME" \
    MS8_HOME="$TMP_MS8_HOME" \
    MS8_DATA_DIR="$TMP_DATA" \
    MS8_CONFIG_DIR="$TMP_CONFIG" \
    MS8_LOG_DIR="$TMP_LOGS" \
    MS8_DOCTOR_ALLOW_DEGRADED="1" \
    OPENCLAW_MEMORY_SESSION_INGEST_ENABLED="0" \
    "$@" >"$stdout_log" 2>"$stderr_log"; then
    echo "[OK] $name"
    PASSED_STEPS+=("$name")
  else
    local code=$?
    echo "[FAIL] $name exit=$code"
    echo "stdout: $stdout_log"
    echo "stderr: $stderr_log"
    echo "TMP_HOME: $TMP_HOME"
    FAILED_STEPS+=("$name")
    return $code
  fi
}

PASSED_STEPS=()
FAILED_STEPS=()

run_step "help" "$TMP_VENV/bin/ms8" --help
if "$TMP_VENV/bin/ms8" --help | grep -q " init "; then
  run_step "init" "$TMP_VENV/bin/ms8" init
else
  echo "[INFO] ms8 init command not found; initialization is implicit on first command."
fi
run_step "doctor" "$TMP_VENV/bin/ms8" doctor
run_step "connect_package_resources" "$TMP_VENV/bin/python" "$SMOKE_DIR/connect_package_resources.py"
run_step "absorb_parser_text" "$TMP_VENV/bin/python" "$SMOKE_DIR/absorb_parser_smoke.py"
run_step "ask_write" "$TMP_VENV/bin/ms8" ask "记住 release isolated test memory"
run_step "ask_search" "$TMP_VENV/bin/ms8" ask "release isolated" --limit 5
run_step "ask_records_written" "$TMP_VENV/bin/python" "$SMOKE_DIR/ask_records_smoke.py"
run_step "clean_dry_run" "$TMP_VENV/bin/ms8" clean --dry-run
run_step "reset_dry_run" "$TMP_VENV/bin/ms8" reset --dry-run
run_step "uninstall_dry_run" "$TMP_VENV/bin/ms8" uninstall --dry-run

if [[ "${#FAILED_STEPS[@]}" -gt 0 ]]; then
  TEST_STATUS="failed"
else
  TEST_STATUS="passed"
fi

echo
echo "===== ISOLATED RELEASE TEST SUMMARY ====="
echo "Wheel: $WHEEL"
echo "Sdist: $SDIST"
echo "Venv: $TMP_VENV"
echo "HOME: $TMP_HOME"
echo "MS8_HOME: $TMP_MS8_HOME"
echo "MS8_DATA_DIR: $TMP_DATA"
echo "MS8_CONFIG_DIR: $TMP_CONFIG"
echo "MS8_LOG_DIR: $TMP_LOGS"
echo "Logs: $LOG_DIR"
echo "Passed steps: ${PASSED_STEPS[*]:-none}"
echo "Failed steps: ${FAILED_STEPS[*]:-none}"
echo "Generated data files:"
find "$TMP_MS8_HOME" -type f | sed 's#^# - #'
echo "Result: $TEST_STATUS"

if [[ "$MODE" == "--cleanup" ]]; then
  # Some security/shadow files are intentionally written read-only. They live
  # under this test-only temp root, so make them removable before cleanup.
  chmod -R u+rwX "$BASE_TMP" 2>/dev/null || true
  if command -v chflags >/dev/null 2>&1; then
    chflags -R nouchg "$BASE_TMP" 2>/dev/null || true
  fi
  rm -rf "$BASE_TMP"
  echo "[INFO] cleanup complete: $BASE_TMP"
else
  echo "[INFO] keeping temp dir: $BASE_TMP"
fi

if [[ "$TEST_STATUS" != "passed" ]]; then
  exit 1
fi
