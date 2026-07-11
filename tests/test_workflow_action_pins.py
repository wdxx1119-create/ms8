from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
ACTION_REF = re.compile(r"^\s*-?\s*uses:\s*([^\s#]+)")
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


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
