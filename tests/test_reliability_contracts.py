from __future__ import annotations

from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib


ROOT = Path(__file__).resolve().parents[1]


def test_pytest_collection_has_one_explicit_contract() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["tool"]["pytest"]["ini_options"]["testpaths"] == [
        "tests",
        "src/ms8/engine_core/tests",
    ]

    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    candidate = (ROOT / ".github" / "workflows" / "release-candidate.yml").read_text(encoding="utf-8")
    checklist = (ROOT / "scripts" / "release_checklist.py").read_text(encoding="utf-8")
    assert "pytest tests/" not in ci
    assert "pytest tests/" not in candidate
    assert '"pytest",\n                "-q"' in checklist


def test_local_release_gate_matches_repository_baselines() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    checklist = (ROOT / "scripts" / "release_checklist.py").read_text(encoding="utf-8")
    shell_wrapper = (ROOT / "scripts" / "release_checklist.sh").read_text(encoding="utf-8")

    assert "line_rate < 80.0" in ci
    assert '"--cov-fail-under=80"' in checklist
    assert '"twine", "check"' in checklist
    assert 'root / "scripts" / "audit_installed_environment.py"' in checklist
    assert '"pip", "check"' in checklist
    assert "wheel-audit-requirements.txt" in checklist
    assert "wheel-audit.json" in checklist
    assert "wheel-audit.log" in checklist
    assert "scripts/release_checklist.py" in shell_wrapper
    assert ".venv/bin/python" not in shell_wrapper


def test_recovery_entry_point_is_packaged() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = pyproject["project"]["scripts"]
    assert scripts["ms8-recovery"] == "ms8.recovery_cli:main"
