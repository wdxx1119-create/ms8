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


def test_release_candidate_preserves_and_blocks_on_security_evidence() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release-candidate.yml").read_text(encoding="utf-8")

    assert "build twine pip-audit" in workflow
    assert "Audit installed-wheel environment" in workflow
    assert "Generate installed-wheel CycloneDX SBOM" in workflow
    assert "Validate installed-wheel security evidence" in workflow
    assert "--format json" in workflow
    assert "--format cyclonedx-json" in workflow
    assert "dist/ms8-${EXPECTED_VERSION}.audit.json" in workflow
    assert "dist/ms8-${EXPECTED_VERSION}.cdx.json" in workflow
    assert 'assert sbom.get("bomFormat") == "CycloneDX"' in workflow
    assert 'assert "ms8" in names' in workflow
    assert "shasum -a 256 *.whl *.tar.gz *.audit.json *.cdx.json > SHA256SUMS" in workflow
    assert "Upload release security evidence" in workflow
    assert "Enforce release security evidence gate" in workflow
    assert 'if [[ "${{ steps.release_audit.outcome }}" != "success" ]]' in workflow
    assert 'if [[ "${{ steps.release_sbom.outcome }}" != "success" ]]' in workflow
    assert 'if [[ "${{ steps.validate_release_evidence.outcome }}" != "success" ]]' in workflow
