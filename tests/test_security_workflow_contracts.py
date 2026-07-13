from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
ACTION_REF = re.compile(r"^\s*-?\s*uses:\s*([^\s#]+)")
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


def _workflow(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def test_external_github_actions_are_pinned_to_full_commit_shas() -> None:
    violations: list[str] = []

    for workflow in sorted(WORKFLOWS.glob("*.yml")):
        for line_number, line in enumerate(
            workflow.read_text(encoding="utf-8").splitlines(), start=1
        ):
            match = ACTION_REF.match(line)
            if match is None:
                continue

            reference = match.group(1)
            if reference.startswith("./"):
                continue
            if "@" not in reference:
                violations.append(
                    f"{workflow.relative_to(ROOT)}:{line_number}: missing @ref: {reference}"
                )
                continue

            action, ref = reference.rsplit("@", 1)
            if not action or FULL_SHA.fullmatch(ref) is None:
                violations.append(
                    f"{workflow.relative_to(ROOT)}:{line_number}: "
                    f"external action must use a 40-character commit SHA: {reference}"
                )

    assert not violations, "\n".join(violations)


def test_dependency_audit_is_isolated_blocking_and_evidence_preserving() -> None:
    workflow = _workflow("dependency-audit.yml")

    assert ".audit-target-venv" in workflow
    assert ".audit-tool-venv" in workflow
    assert ".audit-target-venv/bin/python -m pip install --quiet --upgrade pip setuptools wheel" in workflow
    assert "scripts/audit_installed_environment.py" in workflow
    assert "--target-python .audit-target-venv/bin/python" in workflow
    assert "--requirements audit-requirements.txt" in workflow
    assert "--json-report pip-audit.json" in workflow
    assert "--sbom ms8-dependencies.cdx.json" in workflow
    assert "dependency-audit.log" in workflow
    assert "Upload dependency security artifacts" in workflow
    assert "Enforce dependency security gate" in workflow
    assert 'if [[ "${{ steps.audit.outcome }}" != "success" ]]' in workflow
    assert "exit 1" in workflow


def test_runtime_audit_tool_is_strict_pinned_and_excludes_the_project_root() -> None:
    audit_tool = (ROOT / "scripts" / "audit_installed_environment.py").read_text(encoding="utf-8")

    assert '"--strict"' in audit_tool
    assert '"--requirement"' in audit_tool
    assert '"--no-deps"' in audit_tool
    assert "_runtime_closure" in audit_tool
    assert "if key == root_key" in audit_tool
    assert 'metadata["component"]' in audit_tool
    assert '"type": "application"' in audit_tool


def test_release_candidate_audits_attests_preserves_evidence_and_blocks() -> None:
    workflow = _workflow("release-candidate.yml")

    assert "python -m pip install build twine pip-audit" in workflow
    assert workflow.count(
        '"$ENV_DIR/bin/python" -m pip install --quiet --upgrade pip setuptools wheel'
    ) >= 2
    assert "Audit installed wheel runtime dependency closure" in workflow
    assert "scripts/audit_installed_environment.py" in workflow
    assert "id: wheel_audit" in workflow
    assert "continue-on-error: true" in workflow
    assert "dist/wheel-audit-requirements.txt" in workflow
    assert "dist/wheel-audit.json" in workflow
    assert "dist/wheel-audit.log" in workflow
    assert "Validate installed-wheel CycloneDX SBOM" in workflow
    assert "id: sbom_validation" in workflow
    assert "assert payload.get('bomFormat') == 'CycloneDX'" in workflow
    assert "root.get('version') == os.environ['EXPECTED_VERSION']" in workflow
    assert 'shasum -a 256 "${files[@]}" > SHA256SUMS' in workflow
    assert "actions/attest@a1948c3f048ba23858d222213b7c278aabede763" in workflow
    assert "id: provenance_attestation" in workflow
    assert "id: sbom_attestation" in workflow
    assert "sbom-path: dist/ms8-${{ steps.project_version.outputs.value }}.cdx.json" in workflow
    assert "Upload release candidate evidence" in workflow
    assert "if: always()" in workflow
    assert "Enforce installed-wheel security and provenance gate" in workflow
    assert "steps.wheel_audit.outcome" in workflow
    assert "steps.sbom_validation.outcome" in workflow
    assert "steps.checksums.outcome" in workflow
    assert "steps.provenance_attestation.outcome" in workflow
    assert "steps.sbom_attestation.outcome" in workflow


def test_release_candidate_uses_job_scoped_permissions_and_reports_status() -> None:
    workflow = _workflow("release-candidate.yml")
    top_permissions = workflow.split("concurrency:", 1)[0].split("permissions:", 1)[1]

    assert "contents: read" in top_permissions
    assert "id-token: write" not in top_permissions
    assert "attestations: write" not in top_permissions
    assert "statuses: write" not in top_permissions

    release_job = workflow.split("  release-artifacts:", 1)[1].split("  candidate-status:", 1)[0]
    assert "id-token: write" in release_job
    assert "attestations: write" in release_job
    assert "statuses: write" not in release_job

    status_job = workflow.split("  candidate-status:", 1)[1]
    assert "if: always()" in status_job
    assert "statuses: write" in status_job
    assert "id-token: write" not in status_job
    assert '"context": "release-candidate/aggregate"' in status_job
    assert "GITHUB_SHA" in status_job
    assert "needs.release-artifacts.result" in status_job


def test_release_candidate_only_runs_for_explicit_candidates() -> None:
    workflow = _workflow("release-candidate.yml")
    trigger_block = workflow.split("permissions:", 1)[0]

    assert '"candidate/**"' in trigger_block
    assert '"rc-*"' in trigger_block
    assert "workflow_dispatch:" in trigger_block
    assert "pull_request:" not in trigger_block
    assert "- main" not in trigger_block
    assert "runs-on: ubuntu-latest" in workflow
    assert workflow.count("runs-on: macos-latest") == 1
