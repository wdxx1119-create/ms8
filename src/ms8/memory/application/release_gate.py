"""Fail-closed release-readiness evaluation for memory-ledger-v1.

The evaluator consumes committed validation evidence only. It never enables a runtime
format, performs a migration, publishes a package, or mutates user data. A release is
ready only when every required phase and every supported platform has explicit PASS
evidence with the required safety invariants. Final workflows can bind reports to the
candidate commit ancestry and to exact wheel/sdist SHA-256 digests.
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass
from pathlib import Path

PASS_MARKER = "Overall result: **PASS**"
_SHA_PATTERN = re.compile(r"Tested source SHA:\s*`?([0-9a-f]{40})`?")

REQUIRED_PHASE_REPORTS: Mapping[str, str] = {
    "production_characterization": "docs/validation/MEMORY_LEDGER_V1_CHARACTERIZATION_RESULT.md",
    "cli_mcp_characterization": "docs/validation/MEMORY_LEDGER_V1_CLI_MCP_CHARACTERIZATION_RESULT.md",
    "production_migration_controls": "docs/validation/MEMORY_LEDGER_V1_PHASE4B_SAFETY_RESULT.md",
    "physical_purge_controls": "docs/validation/MEMORY_LEDGER_V1_PHASE4B_PURGE_RESULT.md",
    "retrieval_context": "docs/validation/MEMORY_LEDGER_V1_PHASE5_RETRIEVAL_CONTEXT_RESULT.md",
    "compatibility_adapter": "docs/validation/MEMORY_LEDGER_V1_PHASE5_COMPAT_RESULT.md",
    "cli_explain": "docs/validation/MEMORY_LEDGER_V1_PHASE5_CLI_EXPLAIN_RESULT.md",
    "projection_rebuild_recovery": "docs/validation/MEMORY_LEDGER_V1_PHASE6_REBUILD_RECOVERY_RESULT.md",
    "destructive_recovery": "docs/validation/MEMORY_LEDGER_V1_PHASE6_DESTRUCTIVE_RESULT.md",
    "final_operations": "docs/validation/MEMORY_LEDGER_V1_FINAL_OPERATIONS_RESULT.md",
    "performance_baseline": "docs/validation/MEMORY_LEDGER_V1_PERFORMANCE_RESULT.md",
    "public_safety": "docs/validation/MEMORY_LEDGER_V1_PUBLIC_SAFETY_RESULT.md",
}

REQUIRED_PHASE_MARKERS: Mapping[str, tuple[str, ...]] = {
    "compatibility_adapter": (
        "Legacy route remains default: yes",
        "Ledger-v1 compatibility writes enabled: no",
        "Real user runtime accessed: no",
    ),
    "cli_explain": (
        "Ledger-v1 compatibility writes enabled: no",
        "Legacy runtime remains default: yes",
        "Real user runtime accessed: no",
    ),
    "projection_rebuild_recovery": (
        "Authoritative ledger rewritten by rebuild service: no",
        "Projection rebuild default: dry-run",
        "Ledger-v1 enabled by default: no",
    ),
    "destructive_recovery": (
        "Destructive operations confined to temporary runtimes: yes",
        "Non-tail ledger corruption auto-removed: no",
        "Ledger-v1 enabled by default: no",
    ),
    "final_operations": (
        "Main ms8 CLI integration: PASS",
        "Doctor integration: PASS",
        "Real user runtime accessed: no",
        "Ledger-v1 enabled by default: no",
    ),
    "performance_baseline": (
        "Budget pass: true",
        "Real user runtime accessed: no",
        "Ledger-v1 enabled by default: no",
    ),
    "public_safety": (
        "Blocking findings: 0",
        "Credentials or private keys accepted: no",
        "Closed policy-core source accepted: no",
        "Public repository modified: no",
    ),
}

REQUIRED_PLATFORM_REPORTS: Mapping[str, str] = {
    "macos": "docs/validation/MEMORY_LEDGER_V1_PLATFORM_MACOS_RESULT.md",
    "windows": "docs/validation/MEMORY_LEDGER_V1_PLATFORM_WINDOWS_RESULT.md",
}

PLATFORM_NAMES: Mapping[str, str] = {
    "macos": "macOS",
    "windows": "Windows",
    "linux": "Linux",
}

COMMON_PLATFORM_MARKERS = (
    "Wheel/sdist build: PASS",
    "Twine check: PASS",
    "Clean install smoke: PASS",
    "PyPI publish performed: no",
    "Ledger-v1 enabled by default: no",
)


@dataclass(frozen=True, slots=True)
class ReleaseEvidence:
    name: str
    kind: str
    relative_path: str
    exists: bool
    passed: bool
    missing_markers: tuple[str, ...]
    tested_sha: str | None = None
    sha_accepted: bool = True
    artifact_digests_accepted: bool = True

    @property
    def accepted(self) -> bool:
        return (
            self.exists
            and self.passed
            and not self.missing_markers
            and self.sha_accepted
            and self.artifact_digests_accepted
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "kind": self.kind,
            "relative_path": self.relative_path,
            "exists": self.exists,
            "passed": self.passed,
            "missing_markers": list(self.missing_markers),
            "tested_sha": self.tested_sha,
            "sha_accepted": self.sha_accepted,
            "artifact_digests_accepted": self.artifact_digests_accepted,
            "accepted": self.accepted,
        }


@dataclass(frozen=True, slots=True)
class ReleaseGateDecision:
    phase_ready: bool
    platform_ready: bool
    release_ready: bool
    status: str
    reason_codes: tuple[str, ...]
    evidence: tuple[ReleaseEvidence, ...]
    candidate_sha: str | None = None
    wheel_sha256: str | None = None
    sdist_sha256: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "phase_ready": self.phase_ready,
            "platform_ready": self.platform_ready,
            "release_ready": self.release_ready,
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "candidate_sha": self.candidate_sha,
            "wheel_sha256": self.wheel_sha256,
            "sdist_sha256": self.sdist_sha256,
            "evidence": [item.to_dict() for item in self.evidence],
        }


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _tested_sha(text: str) -> str | None:
    match = _SHA_PATTERN.search(text)
    return match.group(1) if match is not None else None


def _check_report(
    repository_root: Path,
    *,
    name: str,
    kind: str,
    relative_path: str,
    required_markers: Sequence[str],
    candidate_sha: str | None,
    allowed_source_shas: Set[str] | None,
    require_exact_candidate: bool,
    wheel_sha256: str | None,
    sdist_sha256: str | None,
) -> ReleaseEvidence:
    report_path = repository_root / relative_path
    text = _read_text(report_path)
    if text is None:
        return ReleaseEvidence(
            name=name,
            kind=kind,
            relative_path=relative_path,
            exists=False,
            passed=False,
            missing_markers=tuple(required_markers),
            sha_accepted=candidate_sha is None,
            artifact_digests_accepted=wheel_sha256 is None and sdist_sha256 is None,
        )
    report_sha = _tested_sha(text)
    sha_accepted = True
    if candidate_sha is not None:
        if require_exact_candidate:
            sha_accepted = report_sha == candidate_sha
        elif allowed_source_shas is not None:
            sha_accepted = report_sha in allowed_source_shas
        else:
            sha_accepted = report_sha == candidate_sha
    artifact_accepted = True
    if kind == "platform":
        if wheel_sha256 is not None:
            artifact_accepted = artifact_accepted and f"Wheel SHA-256: `{wheel_sha256}`" in text
        if sdist_sha256 is not None:
            artifact_accepted = artifact_accepted and f"Source distribution SHA-256: `{sdist_sha256}`" in text
    return ReleaseEvidence(
        name=name,
        kind=kind,
        relative_path=relative_path,
        exists=True,
        passed=PASS_MARKER in text,
        missing_markers=tuple(marker for marker in required_markers if marker not in text),
        tested_sha=report_sha,
        sha_accepted=sha_accepted,
        artifact_digests_accepted=artifact_accepted,
    )


def _reason_codes(evidence: Sequence[ReleaseEvidence]) -> tuple[str, ...]:
    reasons: list[str] = []
    for item in evidence:
        prefix = item.kind
        if not item.exists:
            reasons.append(f"{prefix}_missing:{item.name}")
            continue
        if not item.passed:
            reasons.append(f"{prefix}_failed:{item.name}")
        if item.missing_markers:
            reasons.append(f"{prefix}_invariant_missing:{item.name}")
        if not item.sha_accepted:
            reasons.append(f"{prefix}_sha_unbound:{item.name}")
        if not item.artifact_digests_accepted:
            reasons.append(f"{prefix}_artifact_mismatch:{item.name}")
    return tuple(reasons)


def evaluate_release_gate(
    repository_root: Path,
    *,
    phase_reports: Mapping[str, str] = REQUIRED_PHASE_REPORTS,
    phase_markers: Mapping[str, Sequence[str]] = REQUIRED_PHASE_MARKERS,
    platform_reports: Mapping[str, str] = REQUIRED_PLATFORM_REPORTS,
    candidate_sha: str | None = None,
    allowed_source_shas: Set[str] | None = None,
    wheel_sha256: str | None = None,
    sdist_sha256: str | None = None,
) -> ReleaseGateDecision:
    """Evaluate committed phase and platform evidence without mutating the repository."""

    root = Path(repository_root).resolve()
    normalized_candidate = str(candidate_sha or "").strip() or None
    phase_evidence = tuple(
        _check_report(
            root,
            name=name,
            kind="phase",
            relative_path=relative_path,
            required_markers=phase_markers.get(name, ()),
            candidate_sha=normalized_candidate,
            allowed_source_shas=allowed_source_shas,
            require_exact_candidate=name in {"final_operations", "performance_baseline", "public_safety"},
            wheel_sha256=None,
            sdist_sha256=None,
        )
        for name, relative_path in phase_reports.items()
    )
    platform_evidence = tuple(
        _check_report(
            root,
            name=name,
            kind="platform",
            relative_path=relative_path,
            required_markers=(f"Platform: {PLATFORM_NAMES.get(name, name)}", *COMMON_PLATFORM_MARKERS),
            candidate_sha=normalized_candidate,
            allowed_source_shas=allowed_source_shas,
            require_exact_candidate=True,
            wheel_sha256=wheel_sha256,
            sdist_sha256=sdist_sha256,
        )
        for name, relative_path in platform_reports.items()
    )
    evidence = (*phase_evidence, *platform_evidence)
    phase_ready = all(item.accepted for item in phase_evidence)
    platform_ready = all(item.accepted for item in platform_evidence)
    release_ready = phase_ready and platform_ready
    return ReleaseGateDecision(
        phase_ready=phase_ready,
        platform_ready=platform_ready,
        release_ready=release_ready,
        status="ready" if release_ready else "hold",
        reason_codes=_reason_codes(evidence),
        evidence=evidence,
        candidate_sha=normalized_candidate,
        wheel_sha256=wheel_sha256,
        sdist_sha256=sdist_sha256,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate MS8 memory-ledger-v1 release evidence")
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--candidate-sha", default="")
    parser.add_argument("--allowed-shas-file", type=Path, default=None)
    parser.add_argument("--wheel-sha256", default="")
    parser.add_argument("--sdist-sha256", default="")
    parser.add_argument(
        "--allow-hold",
        action="store_true",
        help="return success for an accurately reported HOLD decision",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    allowed: set[str] | None = None
    if args.allowed_shas_file is not None:
        allowed = {
            line.strip()
            for line in args.allowed_shas_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
    decision = evaluate_release_gate(
        args.repository_root,
        candidate_sha=args.candidate_sha or None,
        allowed_source_shas=allowed,
        wheel_sha256=args.wheel_sha256 or None,
        sdist_sha256=args.sdist_sha256 or None,
    )
    rendered = json.dumps(decision.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    if decision.release_ready or args.allow_hold:
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "COMMON_PLATFORM_MARKERS",
    "PASS_MARKER",
    "REQUIRED_PHASE_MARKERS",
    "REQUIRED_PHASE_REPORTS",
    "REQUIRED_PLATFORM_REPORTS",
    "ReleaseEvidence",
    "ReleaseGateDecision",
    "evaluate_release_gate",
]
