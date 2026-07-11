from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="backslashreplace")


def _run(args: Sequence[str], env: dict[str, str]) -> None:
    printable = " ".join(args)
    print(f"\n$ {printable}")
    completed = subprocess.run(
        list(args),
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"command failed with exit code {completed.returncode}: {printable}")


def _make_writable(path: Path) -> None:
    try:
        path.chmod(path.stat().st_mode | stat.S_IWUSR)
    except OSError:
        pass


def _remove_tree(path: Path) -> None:
    if not path.exists():
        return
    for item in path.rglob("*"):
        _make_writable(item)
    _make_writable(path)
    shutil.rmtree(path)


def main() -> int:
    _configure_stdio()
    parser = argparse.ArgumentParser(
        description="Run a basic MS8 CLI flow in an isolated temporary runtime."
    )
    parser.add_argument(
        "--base-dir",
        type=Path,
        help="Use this test-only directory instead of creating a temporary one.",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Keep the generated isolated runtime for inspection.",
    )
    args = parser.parse_args()

    if args.base_dir is None:
        base = Path(tempfile.mkdtemp(prefix="ms8 example "))
    else:
        base = args.base_dir.expanduser().resolve()
        if base.exists():
            raise SystemExit(
                f"Refusing to replace an existing directory: {base}. "
                "Choose a new test-only path."
            )
        base.mkdir(parents=True)

    home = base / "home"
    ms8_home = home / ".ms8"
    data = ms8_home / "data"
    config = ms8_home / "config"
    logs = ms8_home / "logs"
    for directory in (home, data, config, logs):
        directory.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "MS8_HOME": str(ms8_home),
            "MS8_DATA_DIR": str(data),
            "MS8_CONFIG_DIR": str(config),
            "MS8_LOG_DIR": str(logs),
            "MS8_DOCTOR_ALLOW_DEGRADED": "1",
            "OPENCLAW_MEMORY_SESSION_INGEST_ENABLED": "0",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        }
    )

    command = [sys.executable, "-m", "ms8"]
    print(f"Using isolated runtime: {ms8_home}")

    try:
        _run([*command, "--help"], env)
        _run([*command, "version"], env)
        _run([*command, "doctor"], env)
        _run([*command, "ask", "记住 isolated example prefers Python"], env)
        _run([*command, "ask", "isolated example prefers", "--limit", "5"], env)
    finally:
        if args.keep:
            print(f"\nKept isolated runtime: {base}")
        else:
            _remove_tree(base)
            print(f"\nRemoved isolated runtime: {base}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
