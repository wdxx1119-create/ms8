from __future__ import annotations

from pathlib import Path

from ms8.memory.application.release_gate import COMMON_PLATFORM_MARKERS, evaluate_release_gate


def _write_report(
    root: Path,
    relative_path: str,
    *,
    passed: bool = True,
    markers: tuple[str, ...] = (),
    tested_sha: str | None = None,
) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    overall = "PASS" if passed else "FAIL"
    lines = ["# Validation result", "", f"- Overall result: **{overall}**"]
    if tested_sha is not None:
        lines.append(f"- Tested source SHA: `{tested_sha}`")
    lines.extend(f"- {marker}" for marker in markers)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _platform_markers(name: str) -> tuple[str, ...]:
    display = {"macos": "macOS", "windows": "Windows", "linux": "Linux"}[name]
    return (f"Platform: {display}", *COMMON_PLATFORM_MARKERS)


def test_release_gate_holds_when_windows_and_linux_evidence_are_missing(tmp_path: Path) -> None:
    phase_reports = {"core": "evidence/core.md"}
    phase_markers = {"core": ("Authority remains legacy: yes",)}
    platform_reports = {
        "macos": "evidence/macos.md",
        "windows": "evidence/windows.md",
        "linux": "evidence/linux.md",
    }
    _write_report(
        tmp_path,
        phase_reports["core"],
        markers=("Authority remains legacy: yes",),
    )
    _write_report(
        tmp_path,
        platform_reports["macos"],
        markers=_platform_markers("macos"),
    )

    decision = evaluate_release_gate(
        tmp_path,
        phase_reports=phase_reports,
        phase_markers=phase_markers,
        platform_reports=platform_reports,
    )

    assert decision.phase_ready is True
    assert decision.platform_ready is False
    assert decision.release_ready is False
    assert decision.status == "hold"
    assert decision.reason_codes == (
        "platform_missing:windows",
        "platform_missing:linux",
    )


def test_release_gate_becomes_ready_only_with_all_platform_passes(tmp_path: Path) -> None:
    phase_reports = {
        "migration": "evidence/migration.md",
        "recovery": "evidence/recovery.md",
    }
    phase_markers = {
        "migration": ("Real runtime accessed: no",),
        "recovery": ("Default is dry-run: yes",),
    }
    platform_reports = {
        "macos": "evidence/macos.md",
        "windows": "evidence/windows.md",
        "linux": "evidence/linux.md",
    }
    _write_report(
        tmp_path,
        phase_reports["migration"],
        markers=("Real runtime accessed: no",),
    )
    _write_report(
        tmp_path,
        phase_reports["recovery"],
        markers=("Default is dry-run: yes",),
    )
    for platform, report in platform_reports.items():
        _write_report(tmp_path, report, markers=_platform_markers(platform))

    decision = evaluate_release_gate(
        tmp_path,
        phase_reports=phase_reports,
        phase_markers=phase_markers,
        platform_reports=platform_reports,
    )

    assert decision.phase_ready is True
    assert decision.platform_ready is True
    assert decision.release_ready is True
    assert decision.status == "ready"
    assert decision.reason_codes == ()
    assert all(item.accepted for item in decision.evidence)


def test_failed_phase_and_missing_safety_marker_are_reported_separately(tmp_path: Path) -> None:
    phase_reports = {
        "failed": "evidence/failed.md",
        "unsafe": "evidence/unsafe.md",
    }
    phase_markers = {
        "failed": (),
        "unsafe": ("No production write route change: yes",),
    }
    _write_report(tmp_path, phase_reports["failed"], passed=False)
    _write_report(tmp_path, phase_reports["unsafe"], passed=True)

    decision = evaluate_release_gate(
        tmp_path,
        phase_reports=phase_reports,
        phase_markers=phase_markers,
        platform_reports={},
    )

    assert decision.phase_ready is False
    assert decision.platform_ready is True
    assert decision.release_ready is False
    assert decision.reason_codes == (
        "phase_failed:failed",
        "phase_invariant_missing:unsafe",
    )


def test_platform_report_with_pass_but_missing_packaging_marker_is_rejected(tmp_path: Path) -> None:
    platform_reports = {"macos": "evidence/macos.md"}
    incomplete = tuple(marker for marker in _platform_markers("macos") if marker != "Twine check: PASS")
    _write_report(tmp_path, platform_reports["macos"], markers=incomplete)

    decision = evaluate_release_gate(
        tmp_path,
        phase_reports={},
        phase_markers={},
        platform_reports=platform_reports,
    )

    assert decision.phase_ready is True
    assert decision.platform_ready is False
    assert decision.reason_codes == ("platform_invariant_missing:macos",)
    evidence = decision.to_dict()["evidence"]
    assert isinstance(evidence, list)
    assert evidence[0]["missing_markers"] == ["Twine check: PASS"]


def test_candidate_ancestry_and_platform_digest_binding_are_required(tmp_path: Path) -> None:
    candidate = "a" * 40
    ancestor = "b" * 40
    unrelated = "c" * 40
    wheel_digest = "1" * 64
    sdist_digest = "2" * 64
    phase_reports = {
        "historical": "evidence/historical.md",
        "final_operations": "evidence/final.md",
    }
    phase_markers = {"historical": (), "final_operations": ()}
    platform_reports = {"macos": "evidence/macos.md"}
    _write_report(tmp_path, phase_reports["historical"], tested_sha=ancestor)
    _write_report(tmp_path, phase_reports["final_operations"], tested_sha=candidate)
    _write_report(
        tmp_path,
        platform_reports["macos"],
        tested_sha=candidate,
        markers=(
            *_platform_markers("macos"),
            f"Wheel SHA-256: `{wheel_digest}`",
            f"Source distribution SHA-256: `{sdist_digest}`",
        ),
    )

    ready = evaluate_release_gate(
        tmp_path,
        phase_reports=phase_reports,
        phase_markers=phase_markers,
        platform_reports=platform_reports,
        candidate_sha=candidate,
        allowed_source_shas={candidate, ancestor},
        wheel_sha256=wheel_digest,
        sdist_sha256=sdist_digest,
    )
    assert ready.release_ready is True
    assert ready.reason_codes == ()

    rejected = evaluate_release_gate(
        tmp_path,
        phase_reports=phase_reports,
        phase_markers=phase_markers,
        platform_reports=platform_reports,
        candidate_sha=unrelated,
        allowed_source_shas={unrelated},
        wheel_sha256="3" * 64,
        sdist_sha256="4" * 64,
    )
    assert rejected.release_ready is False
    assert "phase_sha_unbound:historical" in rejected.reason_codes
    assert "phase_sha_unbound:final_operations" in rejected.reason_codes
    assert "platform_sha_unbound:macos" in rejected.reason_codes
    assert "platform_artifact_mismatch:macos" in rejected.reason_codes
