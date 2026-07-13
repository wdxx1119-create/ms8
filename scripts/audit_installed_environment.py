#!/usr/bin/env python3
"""Audit the exact runtime dependency closure of an installed MS8 artifact.

The project distribution itself may be an unpublished release candidate, so it
must not be sent to the vulnerability service. Instead, this tool reads the
installed project's Requires-Dist metadata, resolves the installed runtime
closure, writes an exact pinned requirements file, audits that file in strict
no-dependency-resolution mode, and adds the project as the CycloneDX root
component.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Iterable
from importlib.metadata import Distribution, distributions
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def _target_site_packages(target_python: Path) -> Path:
    code = "import sysconfig; print(sysconfig.get_paths()['purelib'])"
    output = subprocess.check_output([str(target_python), "-c", code], text=True).strip()
    path = Path(output).resolve()
    if not path.is_dir():
        raise RuntimeError(f"target site-packages does not exist: {path}")
    return path


def _distribution_name(distribution: Distribution) -> str:
    name = distribution.metadata.get("Name")
    if not name:
        raise RuntimeError("installed distribution is missing Name metadata")
    return str(name)


def _installed_index(site_packages: Path) -> dict[str, Distribution]:
    index: dict[str, Distribution] = {}
    for distribution in distributions(path=[str(site_packages)]):
        name = _distribution_name(distribution)
        canonical = canonicalize_name(name)
        existing = index.get(canonical)
        if existing is not None and existing.version != distribution.version:
            raise RuntimeError(
                f"multiple installed versions for {name}: {existing.version}, {distribution.version}"
            )
        index[canonical] = distribution
    return index


def _marker_applies(requirement: Requirement, environment: dict[str, str]) -> bool:
    if requirement.marker is None:
        return True
    marker_environment = dict(environment)
    marker_environment["extra"] = ""
    return bool(requirement.marker.evaluate(marker_environment))


def _runtime_closure(
    *,
    project_name: str,
    installed: dict[str, Distribution],
) -> list[Distribution]:
    root_key = canonicalize_name(project_name)
    root = installed.get(root_key)
    if root is None:
        raise RuntimeError(f"installed project distribution not found: {project_name}")

    environment = default_environment()
    resolved: dict[str, Distribution] = {}
    queue: list[Requirement] = []
    for raw in root.requires or []:
        requirement = Requirement(raw)
        if _marker_applies(requirement, environment):
            queue.append(requirement)

    while queue:
        requirement = queue.pop(0)
        key = canonicalize_name(requirement.name)
        if key == root_key or key in resolved:
            continue
        dependency = installed.get(key)
        if dependency is None:
            raise RuntimeError(
                f"runtime dependency declared by {project_name} is not installed: {requirement}"
            )
        resolved[key] = dependency
        for raw in dependency.requires or []:
            child = Requirement(raw)
            if _marker_applies(child, environment):
                queue.append(child)

    return [resolved[key] for key in sorted(resolved)]


def _write_requirements(path: Path, dependencies: Iterable[Distribution]) -> list[str]:
    lines = [f"{_distribution_name(item)}=={item.version}" for item in dependencies]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return lines


def _run_audit(
    *,
    requirements: Path,
    json_report: Path,
    sbom: Path,
    log: Path,
) -> None:
    commands = (
        [
            sys.executable,
            "-m",
            "pip_audit",
            "--strict",
            "--requirement",
            str(requirements),
            "--no-deps",
            "--progress-spinner",
            "off",
            "--format",
            "json",
            "--output",
            str(json_report),
        ],
        [
            sys.executable,
            "-m",
            "pip_audit",
            "--strict",
            "--requirement",
            str(requirements),
            "--no-deps",
            "--progress-spinner",
            "off",
            "--format",
            "cyclonedx-json",
            "--output",
            str(sbom),
        ],
    )
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w", encoding="utf-8") as handle:
        for command in commands:
            handle.write(f"[RUN] {' '.join(command)}\n")
            handle.flush()
            completed = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
            handle.write(completed.stdout)
            handle.flush()
            print(completed.stdout, end="")
            if completed.returncode != 0:
                raise RuntimeError(
                    f"pip-audit failed with exit code {completed.returncode}; see {log}"
                )


def _augment_sbom_root(*, sbom: Path, project_name: str, project_version: str) -> None:
    payload = json.loads(sbom.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("CycloneDX SBOM root must be a JSON object")
    normalized_name = canonicalize_name(project_name)
    root_ref = f"pkg:pypi/{normalized_name}@{project_version}"
    metadata = payload.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        raise RuntimeError("CycloneDX metadata must be a JSON object")
    metadata["component"] = {
        "type": "application",
        "bom-ref": root_ref,
        "name": project_name,
        "version": project_version,
        "purl": root_ref,
    }

    component_refs = []
    for component in payload.get("components", []):
        if isinstance(component, dict) and component.get("bom-ref"):
            component_refs.append(str(component["bom-ref"]))
    dependency_rows = payload.setdefault("dependencies", [])
    if not isinstance(dependency_rows, list):
        raise RuntimeError("CycloneDX dependencies must be a JSON list")
    dependency_rows[:] = [
        row
        for row in dependency_rows
        if not (isinstance(row, dict) and row.get("ref") == root_ref)
    ]
    dependency_rows.append({"ref": root_ref, "dependsOn": sorted(component_refs)})
    sbom.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run(
    *,
    target_python: Path,
    project_name: str,
    project_version: str,
    requirements: Path,
    json_report: Path,
    sbom: Path,
    log: Path,
) -> None:
    site_packages = _target_site_packages(target_python)
    installed = _installed_index(site_packages)
    dependencies = _runtime_closure(project_name=project_name, installed=installed)
    lines = _write_requirements(requirements, dependencies)
    print(f"Auditing {len(lines)} installed runtime dependencies from {site_packages}")
    _run_audit(requirements=requirements, json_report=json_report, sbom=sbom, log=log)
    _augment_sbom_root(sbom=sbom, project_name=project_name, project_version=project_version)
    print(f"Strict runtime dependency audit passed; SBOM root={project_name} {project_version}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-python", type=Path, required=True)
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--project-version", required=True)
    parser.add_argument("--requirements", type=Path, required=True)
    parser.add_argument("--json-report", type=Path, required=True)
    parser.add_argument("--sbom", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        run(
            target_python=args.target_python,
            project_name=args.project_name,
            project_version=args.project_version,
            requirements=args.requirements,
            json_report=args.json_report,
            sbom=args.sbom,
            log=args.log,
        )
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
