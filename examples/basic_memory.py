from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


def run_ms8(args: list[str], env: dict[str, str]) -> str:
    completed = subprocess.run(
        [sys.executable, "-m", "ms8", *args],
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"ms8 {' '.join(args)} failed with exit code {completed.returncode}:\n"
            f"{completed.stdout}\n{completed.stderr}"
        )
    return completed.stdout.strip()


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="ms8-basic-memory-") as raw_root:
        root = Path(raw_root)
        home = root / "home"
        ms8_home = home / ".ms8"
        env = os.environ.copy()
        env.update(
            {
                "HOME": str(home),
                "USERPROFILE": str(home),
                "MS8_HOME": str(ms8_home),
                "MS8_DATA_DIR": str(ms8_home / "data"),
                "MS8_CONFIG_DIR": str(ms8_home / "config"),
                "MS8_LOG_DIR": str(ms8_home / "logs"),
                "MS8_DOCTOR_ALLOW_DEGRADED": "1",
                "OPENCLAW_MEMORY_SESSION_INGEST_ENABLED": "0",
                "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8",
            }
        )

        synthetic_memory = "remember: example user prefers Python for automation"
        print(run_ms8(["ask", synthetic_memory], env))
        print(run_ms8(["ask", "What language does the example user prefer?", "--limit", "5"], env))

        # Import only after the isolated environment is defined.
        os.environ.update(env)
        from ms8.runtime import ensure_runtime_dirs

        records_file = ensure_runtime_dirs()["memories"]
        if not records_file.is_file():
            raise RuntimeError(f"expected canonical record file: {records_file}")
        records_text = records_file.read_text(encoding="utf-8")
        if "example user prefers Python" not in records_text:
            raise RuntimeError("synthetic memory was not persisted")

        print(f"isolated records: {records_file}")
        print("basic memory example completed")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
