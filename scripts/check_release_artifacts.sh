#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

version="$(python3 - <<'PY'
import tomllib
from pathlib import Path
print(tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]["version"])
PY
)"

shopt -s nullglob
wheels=(dist/ms8-"${version}"-py3-none-any.whl)
sdists=(dist/ms8-"${version}".tar.gz)

if [[ ${#wheels[@]} -eq 0 || ${#sdists[@]} -eq 0 ]]; then
  echo "[FAIL] Missing release artifacts in dist/. Run: python3 -m build --no-isolation"
  exit 1
fi

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ms8-artifacts-XXXXXX")"
WHEEL_LIST="$TMP_DIR/wheel_contents.txt"
SDIST_LIST="$TMP_DIR/sdist_contents.txt"

python3 -m zipfile -l "${wheels[0]}" >"$WHEEL_LIST"
tar -tzf "${sdists[0]}" >"$SDIST_LIST"

echo "== Wheel =="
echo "${wheels[0]}"
echo "== Sdist =="
echo "${sdists[0]}"

blocked=(
  ".env"
  ".db"
  ".sqlite"
  ".jsonl"
  "health_report_latest.json"
  "auto_memory_records.jsonl"
  "auto_memory_index.json"
  "auto_memory_review_queue.jsonl"
  "knowledge_graph.db"
  "backup"
  "cache"
  "/Users/"
)

fail=0
for needle in "${blocked[@]}"; do
  if grep -Fqi "$needle" "$WHEEL_LIST"; then
    echo "[FAIL] Wheel contains blocked pattern: $needle"
    fail=1
  fi
  if grep -Fqi "$needle" "$SDIST_LIST"; then
    echo "[FAIL] Sdist contains blocked pattern: $needle"
    fail=1
  fi
done

if [[ $fail -ne 0 ]]; then
  echo "[FAIL] Release artifact content check failed."
  echo "Wheel list: $WHEEL_LIST"
  echo "Sdist list: $SDIST_LIST"
  exit 1
fi

echo "[PASS] Release artifacts are clean."
echo "Wheel list: $WHEEL_LIST"
echo "Sdist list: $SDIST_LIST"
