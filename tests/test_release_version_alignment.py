from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _project_version() -> str:
    match = re.search(r'^version = "([^"]+)"$', _read("pyproject.toml"), re.MULTILINE)
    assert match is not None, "project.version is missing from pyproject.toml"
    return match.group(1)


def test_release_version_surfaces_are_aligned() -> None:
    version = _project_version()
    package_init = _read("src/ms8/__init__.py")
    readme = _read("README.md")
    changelog = _read("CHANGELOG.md")
    release_notes_path = ROOT / "docs" / f"RELEASE_NOTES_{version}.md"

    assert f'__version__ = "{version}"' in package_init
    assert f"version-{version}-blue" in readme
    assert f"## [{version}]" in changelog
    assert release_notes_path.is_file()
    assert f"# MS8 {version} Release Notes" in release_notes_path.read_text(encoding="utf-8")


def test_release_changelog_is_finalized() -> None:
    version = _project_version()
    changelog = _read("CHANGELOG.md")

    assert f"## [Unreleased]\n\n## [{version}]" in changelog
    assert "Release Candidate Notes" not in _read(f"docs/RELEASE_NOTES_{version}.md")
