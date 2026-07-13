"""Run one Python/OS memory-ledger validation case against canonical artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    print("$ " + " ".join(command), flush=True)
    subprocess.run(command, check=True, env=env)


def _venv_python(root: Path) -> Path:
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _venv_cli(root: Path, name: str) -> Path:
    suffix = ".exe" if os.name == "nt" else ""
    directory = "Scripts" if os.name == "nt" else "bin"
    return root / directory / f"{name}{suffix}"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--sdist", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    wheel = args.wheel.resolve()
    sdist = args.sdist.resolve()
    if not wheel.is_file() or not sdist.is_file():
        raise SystemExit("canonical wheel and sdist are required")

    validation_env = os.environ.copy()
    validation_env["PYTHONPATH"] = str(Path.cwd() / "src")
    stages = {
        "source_validation": "PASS",
        "wheel_install": "PASS",
        "wheel_import": "PASS",
        "cli_smoke": "PASS",
        "sdist_install": "PASS",
    }
    error = ""
    try:
        _run([sys.executable, "scripts/validation/run_memory_ledger_validation.py"], env=validation_env)
        with tempfile.TemporaryDirectory(prefix="ms8-platform-wheel-") as directory:
            venv_root = Path(directory)
            _run([sys.executable, "-m", "venv", str(venv_root)])
            python = _venv_python(venv_root)
            try:
                _run([str(python), "-m", "pip", "install", "--quiet", "--upgrade", "pip", "setuptools", "wheel"])
                _run([str(python), "-m", "pip", "install", "--quiet", str(wheel)])
            except subprocess.CalledProcessError:
                stages["wheel_install"] = "FAIL"
                raise
            try:
                _run(
                    [
                        str(python),
                        "-c",
                        (
                            "from importlib.metadata import version; import ms8; "
                            "from ms8.memory.operations_cli import main; "
                            "assert version('ms8') == ms8.__version__; assert main"
                        ),
                    ]
                )
            except subprocess.CalledProcessError:
                stages["wheel_import"] = "FAIL"
                raise
            try:
                _run([str(_venv_cli(venv_root, "ms8")), "version"])
                _run([str(_venv_cli(venv_root, "ms8-memory-ledger")), "--help"])
            except subprocess.CalledProcessError:
                stages["cli_smoke"] = "FAIL"
                raise
        with tempfile.TemporaryDirectory(prefix="ms8-platform-sdist-") as directory:
            venv_root = Path(directory)
            _run([sys.executable, "-m", "venv", str(venv_root)])
            python = _venv_python(venv_root)
            try:
                _run([str(python), "-m", "pip", "install", "--quiet", "--upgrade", "pip", "setuptools", "wheel"])
                _run([str(python), "-m", "pip", "install", "--quiet", str(sdist)])
                _run([str(python), "-c", "import ms8; from ms8.memory.operations_cli import main; assert main"])
            except subprocess.CalledProcessError:
                stages["sdist_install"] = "FAIL"
                raise
    except subprocess.CalledProcessError as exc:
        if all(value == "PASS" for value in stages.values()):
            stages["source_validation"] = "FAIL"
        error = f"command failed with exit code {exc.returncode}"

    passed = all(value == "PASS" for value in stages.values())
    payload = {
        "schema": "ms8.memory-ledger-platform-case.v1",
        "candidate_sha": args.candidate_sha,
        "platform": platform.system(),
        "platform_release": platform.release(),
        "architecture": platform.machine(),
        "python": platform.python_version(),
        "wheel_name": wheel.name,
        "wheel_sha256": _sha256(wheel),
        "sdist_name": sdist.name,
        "sdist_sha256": _sha256(sdist),
        "stages": stages,
        "passed": passed,
        "error": error,
        "real_user_runtime_accessed": False,
        "ledger_v1_enabled_by_default": False,
        "pypi_publish_performed": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    shutil.rmtree(Path.cwd() / ".pytest_cache", ignore_errors=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
