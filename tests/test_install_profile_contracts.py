from __future__ import annotations

from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib


ROOT = Path(__file__).resolve().parents[1]


def _project() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def test_core_install_does_not_force_optional_llm_or_document_parsers() -> None:
    dependencies = set(_project()["dependencies"])

    assert not any(item.lower().startswith("ollama") for item in dependencies)
    assert not any(item.lower().startswith("watchdog") for item in dependencies)
    assert not any(item.lower().startswith("pypdf") for item in dependencies)
    assert not any(item.lower().startswith("python-docx") for item in dependencies)
    assert not any(item.lower().startswith("pytesseract") for item in dependencies)


def test_optional_profiles_have_explicit_capability_closure() -> None:
    extras = _project()["optional-dependencies"]

    assert "ollama>=0.4.0" in extras["llm"]
    assert extras["policy"] == ["ms8-policy-core>=0.1.0,<0.2"]

    absorb = set(extras["absorb"])
    assert {"watchdog>=4.0.0", "pypdf>=4.0.0", "python-docx>=1.1.0"} <= absorb

    ocr = set(extras["ocr"])
    assert absorb <= ocr
    assert {"pytesseract>=0.3.10", "pdf2image>=1.17.0", "pillow>=10.0.0"} <= ocr

    assert set(extras["absorb-ocr"]) == ocr

    full = set(extras["full"])
    assert set(extras["llm"]) <= full
    assert set(extras["policy"]) <= full
    assert ocr <= full


def test_release_version_is_consistent_across_release_surfaces() -> None:
    version = _project()["version"]
    source = (ROOT / "src" / "ms8" / "__init__.py").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    release_notes = (ROOT / "docs" / f"RELEASE_NOTES_{version}.md").read_text(encoding="utf-8")

    assert version == "0.2.18"
    assert f'__version__ = "{version}"' in source
    assert f"version-{version}-blue" in readme
    assert f"dist/ms8-{version}-py3-none-any.whl" in readme
    assert f"## [{version}] - 2026-07-14" in changelog
    assert f"MS8 {version} Release Notes" in release_notes


def test_ci_verifies_each_supported_install_profile() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "install-profile-smoke:" in workflow
    assert "profile: [core, llm, absorb, ocr, policy, full]" in workflow
    assert "core wheel unexpectedly installed the optional ollama dependency" in (
        ROOT / ".github" / "workflows" / "release-candidate.yml"
    ).read_text(encoding="utf-8")
