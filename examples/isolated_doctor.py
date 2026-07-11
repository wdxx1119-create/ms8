from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="ms8-doctor-") as raw_root:
        root = Path(raw_root)
        home = root / "home with spaces"
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

        completed = subprocess.run(
            [sys.executable, "-m", "ms8", "doctor"],
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        print(completed.stdout)
        if completed.stderr:
            print(completed.stderr, file=sys.stderr)
        if completed.returncode not in {0, 1}:
            raise RuntimeError(f"doctor failed with exit code {completed.returncode}")

        if not ms8_home.is_dir():
            raise RuntimeError(f"doctor did not initialize the isolated runtime: {ms8_home}")
        print(f"isolated runtime: {ms8_home}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
