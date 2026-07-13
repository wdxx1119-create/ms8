from __future__ import annotations

import json
from pathlib import Path

from scripts.audit_installed_environment import (
    _augment_sbom_root,
    _distribution_name,
    _installed_index,
    _runtime_closure,
    _write_requirements,
)


def _write_distribution(
    site_packages: Path,
    *,
    name: str,
    version: str,
    requires: tuple[str, ...] = (),
) -> None:
    directory = site_packages / f"{name.replace('-', '_')}-{version}.dist-info"
    directory.mkdir(parents=True)
    lines = [
        "Metadata-Version: 2.1",
        f"Name: {name}",
        f"Version: {version}",
    ]
    lines.extend(f"Requires-Dist: {requirement}" for requirement in requires)
    (directory / "METADATA").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_runtime_closure_excludes_project_and_unrelated_environment_tools(tmp_path: Path) -> None:
    site_packages = tmp_path / "site-packages"
    site_packages.mkdir()
    _write_distribution(
        site_packages,
        name="ms8",
        version="0.2.17",
        requires=("alpha>=1", "ignored; python_version < '0'"),
    )
    _write_distribution(site_packages, name="alpha", version="1.2.0", requires=("beta>=2",))
    _write_distribution(site_packages, name="beta", version="2.4.0")
    _write_distribution(site_packages, name="pip", version="99.0")
    _write_distribution(site_packages, name="setuptools", version="99.0")

    installed = _installed_index(site_packages)
    closure = _runtime_closure(project_name="ms8", installed=installed)

    assert [_distribution_name(item) for item in closure] == ["alpha", "beta"]

    requirements = tmp_path / "requirements.txt"
    lines = _write_requirements(requirements, closure)
    assert lines == ["alpha==1.2.0", "beta==2.4.0"]
    assert requirements.read_text(encoding="utf-8") == "alpha==1.2.0\nbeta==2.4.0\n"


def test_runtime_closure_rejects_missing_declared_dependency(tmp_path: Path) -> None:
    site_packages = tmp_path / "site-packages"
    site_packages.mkdir()
    _write_distribution(
        site_packages,
        name="ms8",
        version="0.2.17",
        requires=("missing-package>=1",),
    )

    installed = _installed_index(site_packages)

    try:
        _runtime_closure(project_name="ms8", installed=installed)
    except RuntimeError as exc:
        assert "is not installed" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("missing runtime dependency was accepted")


def test_cyclonedx_root_is_bound_to_ms8_candidate(tmp_path: Path) -> None:
    sbom = tmp_path / "ms8.cdx.json"
    sbom.write_text(
        json.dumps(
            {
                "bomFormat": "CycloneDX",
                "specVersion": "1.4",
                "components": [
                    {
                        "type": "library",
                        "bom-ref": "pkg:pypi/alpha@1.2.0",
                        "name": "alpha",
                        "version": "1.2.0",
                    }
                ],
                "dependencies": [],
            }
        ),
        encoding="utf-8",
    )

    _augment_sbom_root(sbom=sbom, project_name="ms8", project_version="0.2.17")

    payload = json.loads(sbom.read_text(encoding="utf-8"))
    root = payload["metadata"]["component"]
    assert root == {
        "type": "application",
        "bom-ref": "pkg:pypi/ms8@0.2.17",
        "name": "ms8",
        "version": "0.2.17",
        "purl": "pkg:pypi/ms8@0.2.17",
    }
    assert {"ref": "pkg:pypi/ms8@0.2.17", "dependsOn": ["pkg:pypi/alpha@1.2.0"]} in payload[
        "dependencies"
    ]
