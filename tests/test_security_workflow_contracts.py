from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
ACTION_REFERENCE = re.compile(r"^\s*uses:\s*([^\s#]+)", re.MULTILINE)
IMMUTABLE_EXTERNAL_ACTION = re.compile(r"^[^@]+@[0-9a-f]{40}$")


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_dependency_audit_is_target_only_and_blocking() -> None:
    workflow = _read(".github/workflows/dependency-audit.yml")

    assert ".audit-target-venv" in workflow
    assert ".audit-tool-venv" in workflow
    assert "--strict" in workflow
    assert "--path" in workflow
    assert "pip-audit.json" in workflow
    assert "ms8-dependencies.cdx.json" in workflow
    assert "continue-on-error: true" in workflow
    assert "Enforce dependency security gate" in workflow
    assert 'AUDIT_OUTCOME: ${{ steps.audit.outcome }}' in workflow
    assert 'SBOM_OUTCOME: ${{ steps.dependency_sbom.outcome }}' in workflow


def test_release_candidate_produces_versioned_sbom_and_checksums() -> None:
    workflow = _read(".github/workflows/release-candidate.yml")

    assert "cyclonedx-bom" in workflow
    assert "cyclonedx-py environment" in workflow
    assert 'payload.get(\'bomFormat\') == \'CycloneDX\'' in workflow
    assert "item.get('version') == os.environ['EXPECTED_VERSION']" in workflow
    assert ".cdx.json" in workflow
    assert "SHA256SUMS" in workflow
    assert "Upload independently verified artifacts" in workflow


def test_external_actions_are_pinned_to_full_commit_shas() -> None:
    violations: list[str] = []

    for workflow_path in sorted(WORKFLOWS.glob("*.yml")):
        content = workflow_path.read_text(encoding="utf-8")
        for reference in ACTION_REFERENCE.findall(content):
            if reference.startswith("./"):
                continue
            if not IMMUTABLE_EXTERNAL_ACTION.fullmatch(reference):
                violations.append(f"{workflow_path.relative_to(ROOT)}: {reference}")

    assert not violations, "External Actions must use full commit SHAs:\n" + "\n".join(violations)
