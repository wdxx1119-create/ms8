from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_dependency_audit_is_blocking_and_emits_machine_readable_artifacts() -> None:
    workflow = (ROOT / ".github" / "workflows" / "dependency-audit.yml").read_text(encoding="utf-8")

    assert ".audit-target-venv" in workflow
    assert ".audit-tool-venv" in workflow
    assert '--path "${{ steps.target.outputs.site_packages }}"' in workflow
    assert "--strict" in workflow
    assert "--format json" in workflow
    assert "--format cyclonedx-json" in workflow
    assert "pip-audit.json" in workflow
    assert "ms8-dependencies.cdx.json" in workflow
    assert "Enforce dependency security gate" in workflow
    assert 'if [[ "${{ steps.audit.outcome }}" != "success" ]]' in workflow
    assert 'if [[ "${{ steps.sbom.outcome }}" != "success" ]]' in workflow
    assert "exit 1" in workflow


def test_release_candidate_verifies_and_checksums_cyclonedx_sbom() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release-candidate.yml").read_text(encoding="utf-8")

    assert "build twine pip-audit" in workflow
    assert "Generate and verify installed-wheel CycloneDX SBOM" in workflow
    assert "--format cyclonedx-json" in workflow
    assert 'SBOM_PATH="dist/ms8-${EXPECTED_VERSION}.cdx.json"' in workflow
    assert 'payload.get("bomFormat") == "CycloneDX"' in workflow
    assert 'assert "ms8" in names' in workflow
    assert "shasum -a 256 *.whl *.tar.gz *.cdx.json > SHA256SUMS" in workflow
    assert "dist/ms8-${{ steps.project_version.outputs.value }}.cdx.json" in workflow
