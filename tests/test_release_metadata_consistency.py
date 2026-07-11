from __future__ import annotations

import re
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib


ROOT = Path(__file__).resolve().parents[1]


def _first_match(pattern: str, text: str, label: str) -> str:
    match = re.search(pattern, text, flags=re.MULTILINE)
    assert match is not None, f"missing {label}"
    return match.group(1)


def test_release_version_metadata_is_consistent() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_version = str(pyproject["project"]["version"])

    init_text = (ROOT / "src" / "ms8" / "__init__.py").read_text(encoding="utf-8")
    fallback_version = _first_match(
        r'__version__\s*=\s*"([^"]+)"',
        init_text,
        "source-tree fallback version",
    )

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    badge_version = _first_match(
        r"img\.shields\.io/badge/version-([0-9]+\.[0-9]+\.[0-9]+)-",
        readme,
        "README version badge",
    )
    wheel_example_version = _first_match(
        r"dist/ms8-([0-9]+\.[0-9]+\.[0-9]+)-py3-none-any\.whl",
        readme,
        "README wheel example",
    )

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    changelog_version = _first_match(
        r"^## \[([0-9]+\.[0-9]+\.[0-9]+)\]",
        changelog,
        "latest released changelog version",
    )

    release_notes = ROOT / "docs" / f"RELEASE_NOTES_{package_version}.md"
    assert release_notes.is_file(), f"missing release notes: {release_notes.name}"

    assert {
        package_version,
        fallback_version,
        badge_version,
        wheel_example_version,
        changelog_version,
    } == {package_version}


def test_python_support_metadata_matches_readme_badge() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["requires-python"] == ">=3.10,<3.14"

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "python-3.10--3.13" in readme
