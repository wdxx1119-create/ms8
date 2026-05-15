#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${VENV_DIR:-$ROOT_DIR/.venv}"
MS8_HOME_DIR="${MS8_HOME:-$ROOT_DIR/.ms8_runtime}"
WITH_SERVICE="${WITH_SERVICE:-0}"
MS8_BIN_DIR="${MS8_BIN_DIR:-$HOME/.local/bin}"
MS8_FALLBACK_BIN_DIR="$ROOT_DIR/bin"
MS8_LAUNCHER="$MS8_BIN_DIR/ms8"
SHELL_RC="${SHELL_RC:-$HOME/.zshrc}"
GLOBAL_INSTALL="${GLOBAL_INSTALL:-0}"
GLOBAL_BIN_DIR="${GLOBAL_BIN_DIR:-/usr/local/bin}"
GLOBAL_FORCE_SUDO="${GLOBAL_FORCE_SUDO:-0}"
RUN_UNINSTALL="${RUN_UNINSTALL:-0}"
UNINSTALL_PURGE_DATA="${UNINSTALL_PURGE_DATA:-0}"
UNINSTALL_DRY_RUN="${UNINSTALL_DRY_RUN:-0}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --global)
      GLOBAL_INSTALL=1
      shift
      ;;
    --with-service)
      WITH_SERVICE=1
      shift
      ;;
    --global-force-sudo)
      GLOBAL_INSTALL=1
      GLOBAL_FORCE_SUDO=1
      shift
      ;;
    --ms8-home)
      MS8_HOME_DIR="$2"
      shift 2
      ;;
    --uninstall)
      RUN_UNINSTALL=1
      shift
      ;;
    --purge-data)
      UNINSTALL_PURGE_DATA=1
      shift
      ;;
    --dry-run)
      UNINSTALL_DRY_RUN=1
      shift
      ;;
    *)
      echo "[ms8-install] unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

log() {
  echo "[ms8-install] $*"
}

step() {
  echo
  echo "==> $*"
}

append_if_missing() {
  local file="$1"
  local line="$2"
  mkdir -p "$(dirname "$file")" 2>/dev/null || return 2
  touch "$file" 2>/dev/null || return 2
  if ! grep -Fq "$line" "$file"; then
    printf '\n%s\n' "$line" >> "$file" || return 2
    return 0
  fi
  return 1
}

run_ms8_command() {
  local -a cmd=()
  if command -v ms8 >/dev/null 2>&1; then
    cmd=(ms8)
  elif [[ -x "$VENV_DIR/bin/python" ]]; then
    cmd=("$VENV_DIR/bin/python" -m ms8)
  else
    cmd=("$PYTHON_BIN" -m ms8)
  fi
  PYTHONPATH="${ROOT_DIR}/src:${PYTHONPATH:-}" MS8_HOME="$MS8_HOME_DIR" "${cmd[@]}" "$@"
}

step "MS8 one-click installation"
log "root: $ROOT_DIR"
log "python: $PYTHON_BIN"
log "runtime: $MS8_HOME_DIR"
log "launcher: $MS8_LAUNCHER"
log "shell rc: $SHELL_RC"
log "global install: $GLOBAL_INSTALL"
log "global force sudo: $GLOBAL_FORCE_SUDO"
log "run uninstall: $RUN_UNINSTALL"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  log "ERROR: python not found: $PYTHON_BIN"
  exit 1
fi

if [[ "$RUN_UNINSTALL" == "1" ]]; then
  step "Running uninstall flow (without reinstall)"
  uninstall_args=(uninstall)
  if [[ "$UNINSTALL_DRY_RUN" == "1" ]]; then
    uninstall_args+=(--dry-run)
  else
    uninstall_args+=(--confirm UNINSTALL)
  fi
  if [[ "$UNINSTALL_PURGE_DATA" == "1" ]]; then
    uninstall_args+=(--purge-data)
  fi
  if run_ms8_command "${uninstall_args[@]}"; then
    step "Uninstall complete"
    exit 0
  fi
  log "ERROR: uninstall failed"
  exit 1
fi

step "Checking Python version"
"$PYTHON_BIN" - <<'PY'
import sys
maj, minv = sys.version_info[:2]
if (maj, minv) < (3, 10):
    raise SystemExit("Python 3.10+ is required")
print(f"[ms8-install] python version OK: {maj}.{minv}")
PY

if [[ ! -d "$VENV_DIR" ]]; then
  step "Creating virtual environment"
  log "creating venv: $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

step "Installing dependencies and ms8 package"
source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip setuptools wheel >/dev/null 2>&1 || true

log "installing ms8 package"
if ! python -m pip install -e "$ROOT_DIR"; then
  log "standard install failed, retrying with --no-build-isolation (offline-friendly)"
  python -m pip install --no-build-isolation -e "$ROOT_DIR"
fi

step "Preparing persistent launcher (no source needed)"
launcher_payload="$(mktemp)"
cat > "$launcher_payload" <<EOF
#!/usr/bin/env bash
set -euo pipefail
export MS8_HOME="${MS8_HOME_DIR}"
export OPENCLAW_MEMORY_FAST_START="\${OPENCLAW_MEMORY_FAST_START:-1}"
export PYTHONPATH="${ROOT_DIR}/src:\${PYTHONPATH:-}"
exec "${VENV_DIR}/bin/python" -m ms8 "\$@"
EOF

if [[ "$GLOBAL_INSTALL" == "1" ]]; then
  if mkdir -p "$GLOBAL_BIN_DIR" 2>/dev/null; then
    MS8_BIN_DIR="$GLOBAL_BIN_DIR"
    MS8_LAUNCHER="$MS8_BIN_DIR/ms8"
    log "global launcher target: $MS8_LAUNCHER"
  elif [[ "$GLOBAL_FORCE_SUDO" == "1" ]]; then
    log "trying sudo install to $GLOBAL_BIN_DIR"
    if ! command -v sudo >/dev/null 2>&1; then
      log "ERROR: --global-force-sudo requested, but sudo is not available"
      exit 1
    fi
    if [[ -t 0 ]]; then
      if sudo mkdir -p "$GLOBAL_BIN_DIR" && sudo install -m 0755 "$launcher_payload" "$GLOBAL_BIN_DIR/ms8"; then
        MS8_BIN_DIR="$GLOBAL_BIN_DIR"
        MS8_LAUNCHER="$MS8_BIN_DIR/ms8"
        log "global launcher installed via sudo: $MS8_LAUNCHER"
      else
        log "ERROR: --global-force-sudo requested but sudo install failed"
        exit 1
      fi
    elif sudo -n true 2>/dev/null; then
      sudo mkdir -p "$GLOBAL_BIN_DIR"
      sudo install -m 0755 "$launcher_payload" "$GLOBAL_BIN_DIR/ms8"
      MS8_BIN_DIR="$GLOBAL_BIN_DIR"
      MS8_LAUNCHER="$MS8_BIN_DIR/ms8"
      log "global launcher installed via non-interactive sudo: $MS8_LAUNCHER"
    else
      log "ERROR: --global-force-sudo requested but no TTY and sudo -n denied"
      exit 1
    fi
  else
    log "cannot write $GLOBAL_BIN_DIR, fallback to user/local launcher mode"
  fi
fi
if [[ ! -x "$MS8_LAUNCHER" ]]; then
  if ! mkdir -p "$MS8_BIN_DIR" 2>/dev/null; then
    log "cannot write $MS8_BIN_DIR, fallback to $MS8_FALLBACK_BIN_DIR"
    mkdir -p "$MS8_FALLBACK_BIN_DIR"
    MS8_BIN_DIR="$MS8_FALLBACK_BIN_DIR"
    MS8_LAUNCHER="$MS8_BIN_DIR/ms8"
  fi
  if ! cp "$launcher_payload" "$MS8_LAUNCHER" 2>/dev/null || ! chmod +x "$MS8_LAUNCHER" 2>/dev/null; then
    log "cannot install launcher at $MS8_LAUNCHER, fallback to $MS8_FALLBACK_BIN_DIR"
    mkdir -p "$MS8_FALLBACK_BIN_DIR"
    MS8_BIN_DIR="$MS8_FALLBACK_BIN_DIR"
    MS8_LAUNCHER="$MS8_BIN_DIR/ms8"
    cp "$launcher_payload" "$MS8_LAUNCHER"
    chmod +x "$MS8_LAUNCHER"
  fi
fi
rm -f "$launcher_payload"
log "launcher created: $MS8_LAUNCHER"

step "Persisting PATH + MS8_HOME to shell profile"
added_path=0
added_home=0
if [[ "$MS8_BIN_DIR" != "/usr/local/bin" ]]; then
  if append_if_missing "$SHELL_RC" "export PATH=\"$MS8_BIN_DIR:\$PATH\""; then
    added_path=1
  fi
else
  log "/usr/local/bin is typically already on PATH; skipping PATH profile write"
fi
if append_if_missing "$SHELL_RC" "export MS8_HOME=\"$MS8_HOME_DIR\""; then
  added_home=1
fi
if [[ "$added_path" -eq 1 ]]; then
  log "added PATH entry to $SHELL_RC"
else
  log "PATH entry unchanged (already present or rc not writable): $SHELL_RC"
fi
if [[ "$added_home" -eq 1 ]]; then
  log "added MS8_HOME to $SHELL_RC"
else
  log "MS8_HOME unchanged (already present or rc not writable): $SHELL_RC"
fi

export PYTHONPATH="${ROOT_DIR}/src:${PYTHONPATH:-}"
export MS8_HOME="$MS8_HOME_DIR"
export OPENCLAW_MEMORY_FAST_START=1
export MS8_SHORTCUT_AUTO=1
export PATH="$MS8_BIN_DIR:$PATH"

step "Bootstrapping runtime and onboarding"
ms8 version
ms8 dashboard >/dev/null || true
ms8 connect bootstrap --target claude_desktop --silent || true
ms8 connect verify --target claude_desktop || true
ms8 doctor || true
ms8 demo || true
ms8 shortcut status || true

if [[ "$WITH_SERVICE" == "1" ]]; then
  step "Installing launchd service"
  ms8 service install --interval 1800 || true
fi

step "Installation complete"
log "No manual 'source venv' required: launcher handles runtime"
log "No manual 'export MS8_HOME' required: launcher + shell profile configured"
log "Desktop shortcut status:"
ms8 shortcut status || true
log "MCP connect status:"
ms8 connect verify || true
echo
echo "==> MCP client integration summary"
python - <<'PY'
from __future__ import annotations
import json
import subprocess

clients = ("claude_desktop", "cursor", "windsurf")
try:
    proc = subprocess.run(["ms8", "connect", "verify"], capture_output=True, text=True, check=False)
    payload = json.loads(proc.stdout or "{}")
except Exception as exc:
    print(f"[ms8-install] connect summary unavailable: {exc}")
else:
    details = payload.get("details", {}) if isinstance(payload.get("details", {}), dict) else {}
    overall = bool(payload.get("ok", False))
    print(f"[ms8-install] overall: {'OK' if overall else 'NEEDS_ATTENTION'}")
    for key in clients:
        d = details.get(key, {}) if isinstance(details.get(key, {}), dict) else {}
        flags = [
            bool(d.get("exists", False)),
            bool(d.get("has_mcpServers", False)),
            bool(d.get("has_ms8_server", False)),
            bool(d.get("command_ok", False)),
            bool(d.get("args_ok", False)),
            not bool(d.get("legacy_path_found", False)),
        ]
        ok = all(flags)
        reason = []
        if not d.get("exists", False):
            reason.append("missing_file")
        if not d.get("has_mcpServers", False):
            reason.append("missing_mcpServers")
        if not d.get("has_ms8_server", False):
            reason.append("missing_ms8_memory_server")
        if not d.get("command_ok", False):
            reason.append("command_mismatch")
        if not d.get("args_ok", False):
            reason.append("args_mismatch")
        if bool(d.get("legacy_path_found", False)):
            reason.append("legacy_path_detected")
        suffix = "" if ok else f" ({','.join(reason)})"
        print(f"[ms8-install] - {key}: {'OK' if ok else 'FAIL'}{suffix}")
PY
echo
echo "Try now:"
echo "  ms8 doctor"
echo "  ms8 dashboard"
