#!/usr/bin/env python3
"""Cross-platform local release gate aligned with CI and candidate validation."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import venv
import zipfile
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib


BLOCKED_ARTIFACT_PATTERNS = (
    ".env",
    ".db",
    ".sqlite",
    ".jsonl",
    "health_report_latest.json",
    "auto_memory_records.jsonl",
    "auto_memory_index.json",
    "auto_memory_review_queue.jsonl",
    "knowledge_graph.db",
    "/Users/",
)


def _run(command: list[str], *, root: Path, env: dict[str, str] | None = None) -> None:
    rendered = " ".join(command)
    print(f"[RUN] {rendered}", flush=True)
    subprocess.run(command, cwd=root, env=env, check=True)


def _venv_python(environment: Path) -> Path:
    return environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _project_version(root: Path) -> str:
    payload = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return str(payload["project"]["version"])


def _artifact_names(root: Path, version: str) -> tuple[Path, Path]:
    wheel = root / "dist" / f"ms8-{version}-py3-none-any.whl"
    sdist = root / "dist" / f"ms8-{version}.tar.gz"
    if not wheel.is_file() or not sdist.is_file():
        raise RuntimeError(f"expected release artifacts are missing: {wheel.name}, {sdist.name}")
    return wheel, sdist


def _inspect_artifacts(wheel: Path, sdist: Path) -> None:
    with zipfile.ZipFile(wheel) as bundle:
        wheel_names = bundle.namelist()
    with tarfile.open(sdist, "r:gz") as bundle:
        sdist_names = bundle.getnames()
    for pattern in BLOCKED_ARTIFACT_PATTERNS:
        if any(pattern.lower() in name.lower() for name in wheel_names):
            raise RuntimeError(f"wheel contains blocked pattern: {pattern}")
        if any(pattern.lower() in name.lower() for name in sdist_names):
            raise RuntimeError(f"sdist contains blocked pattern: {pattern}")


def _clean_install(
    root: Path,
    artifact: Path,
    *,
    audit_output: Path | None = None,
    audit_version: str | None = None,
) -> None:
    with tempfile.TemporaryDirectory(prefix="ms8-release-install-") as temporary:
        environment = Path(temporary) / "venv"
        venv.EnvBuilder(with_pip=True, clear=True).create(environment)
        python = _venv_python(environment)
        _run([str(python), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"], root=root)
        _run([str(python), "-m", "pip", "install", str(artifact)], root=root)
        _run([str(python), "-m", "pip", "check"], root=root)
        _run([str(python), "-m", "ms8", "version"], root=root)
        _run([str(python), "-m", "ms8.recovery_cli", "--help"], root=root)
        if audit_output is not None:
            if not audit_version:
                raise RuntimeError("audit_version is required when audit_output is set")
            _run(
                [
                    sys.executable,
                    str(root / "scripts" / "audit_installed_environment.py"),
                    "--target-python",
                    str(python),
                    "--project-name",
                    "ms8",
                    "--project-version",
                    audit_version,
                    "--requirements",
                    str(root / "dist" / "wheel-audit-requirements.txt"),
                    "--json-report",
                    str(root / "dist" / "wheel-audit.json"),
                    "--sbom",
                    str(audit_output),
                    "--log",
                    str(root / "dist" / "wheel-audit.log"),
                ],
                root=root,
            )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_checksums(root: Path, paths: list[Path]) -> Path:
    output = root / "dist" / "SHA256SUMS"
    lines = [f"{_sha256(path)}  {path.name}" for path in paths]
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(output.read_text(encoding="utf-8"), end="")
    return output


def run(*, root: Path) -> None:
    root = root.resolve()
    version = _project_version(root)
    runtime = Path(tempfile.mkdtemp(prefix="ms8-release-runtime-"))
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(root / "src"),
            "MS8_HOME": str(runtime),
            "MS8_DATA_DIR": str(runtime / "data"),
            "MS8_CONFIG_DIR": str(runtime / "config"),
            "MS8_LOG_DIR": str(runtime / "logs"),
            "OPENCLAW_MEMORY_SESSION_INGEST_ENABLED": "0",
            "MS8_DOCTOR_ALLOW_DEGRADED": "1",
        }
    )
    try:
        _run([sys.executable, "-m", "mypy", "src/ms8"], root=root, env=env)
        _run([sys.executable, "-m", "ruff", "check", "src/ms8"], root=root, env=env)
        _run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "--cov=ms8",
                "--cov-report=term-missing",
                "--cov-report=xml",
                "--cov-fail-under=80",
            ],
            root=root,
            env=env,
        )
        _run([sys.executable, "-m", "ms8", "doctor"], root=root, env=env)

        shutil.rmtree(root / "build", ignore_errors=True)
        shutil.rmtree(root / "dist", ignore_errors=True)
        _run([sys.executable, "-m", "build"], root=root, env=env)
        wheel, sdist = _artifact_names(root, version)
        _run([sys.executable, "-m", "twine", "check", str(wheel), str(sdist)], root=root, env=env)
        _inspect_artifacts(wheel, sdist)

        sbom = root / "dist" / f"ms8-{version}.cdx.json"
        _clean_install(root, wheel, audit_output=sbom, audit_version=version)
        _clean_install(root, sdist)
        if not sbom.is_file() or sbom.stat().st_size == 0:
            raise RuntimeError("installed-wheel CycloneDX SBOM was not generated")
        _write_checksums(root, [wheel, sdist, sbom])
    finally:
        shutil.rmtree(runtime, ignore_errors=True)
    print("[DONE] local release checklist passed")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    try:
        run(root=args.root)
    except (OSError, RuntimeError, subprocess.CalledProcessError, zipfile.BadZipFile, tarfile.TarError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
