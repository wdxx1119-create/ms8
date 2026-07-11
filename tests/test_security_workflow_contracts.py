from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_dependency_audit_uses_isolated_target_and_blocks_on_evidence() -> None:
    workflow = (ROOT / ".github" / "workflows" / "dependency-audit.yml").read_text(encoding="utf-8")

    assert "actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10" in workflow
    assert "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1" in workflow
    assert ".audit-target-venv" in workflow
    assert ".audit-tool-venv" in workflow
    assert ".audit-target-venv/bin/python -m pip install --upgrade pip setuptools" in workflow
    assert '--path "${{ steps.target.outputs.site_packages }}"' in workflow
    assert "--strict" in workflow
    assert "--format json" in workflow
    assert "--format cyclonedx-json" in workflow
    assert "pip-audit.json" in workflow
    assert "ms8-dependencies.cdx.json" in workflow
    assert "Upload dependency security evidence" in workflow
    assert "Enforce dependency security gate" in workflow
    assert 'if [[ "${{ steps.audit.outcome }}" != "success" ]]' in workflow
    assert 'if [[ "${{ steps.sbom.outcome }}" != "success" ]]' in workflow


def test_release_candidate_preserves_diagnostics_before_final_gate() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release-candidate.yml").read_text(encoding="utf-8")

    assert "actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5" in workflow
    assert "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065" in workflow
    assert "cyclonedx-bom pip-audit" in workflow
    assert '"$ENV_DIR/bin/python" -m pip install --upgrade pip setuptools' in workflow
    assert "Audit installed-wheel environment" in workflow
    assert "Generate installed-wheel CycloneDX SBOM" in workflow
    assert "Validate installed-wheel security evidence" in workflow
    assert "--format json" in workflow
    assert "cyclonedx-py environment" in workflow
    assert "dist/ms8-${EXPECTED_VERSION}.audit.json" in workflow
    assert "dist/ms8-${EXPECTED_VERSION}.cdx.json" in workflow
    assert 'assert sbom.get("bomFormat") == "CycloneDX"' in workflow
    assert "id: release_checksums" in workflow
    assert "shasum -a 256 *.whl *.tar.gz *.audit.json *.cdx.json > SHA256SUMS" in workflow
    assert "Upload release security evidence" in workflow
    assert "Enforce release security evidence gate" in workflow
    assert 'if [[ "${{ steps.release_audit.outcome }}" != "success" ]]' in workflow
    assert 'if [[ "${{ steps.release_sbom.outcome }}" != "success" ]]' in workflow
    assert 'if [[ "${{ steps.validate_release_evidence.outcome }}" != "success" ]]' in workflow
    assert 'if [[ "${{ steps.release_checksums.outcome }}" != "success" ]]' in workflow
