from __future__ import annotations

from pathlib import Path

from ms8.memory.application.public_safety import audit_public_candidate


_INTERNAL_DOCUMENTS = (
    "docs/development/MEMORY_HYBRID_V1_TASKS.md",
    "docs/development/MEMORY_HYBRID_V1_GAP_REVIEW.md",
)

_PUBLIC_DOCUMENTS = (
    "docs/HYBRID_RETRIEVAL_V1.md",
    "docs/RELEASE_NOTES_0.2.18.md",
)

_FORBIDDEN_PUBLIC_DOC_MARKERS = (
    "staged task plan",
    "target-to-actual gap review",
    "progress reporting rule",
    "ltr v1 preparation",
    "phase 0 —",
    "phase 1 —",
    "phase 2 —",
)


def _hybrid_paths(root: Path) -> tuple[Path, ...]:
    paths: list[Path] = []
    paths.extend((root / "src/ms8/memory/retrieval").rglob("*.py"))
    paths.extend((root / "tests").glob("test_memory_hybrid_*.py"))
    paths.extend(root / relative for relative in _PUBLIC_DOCUMENTS)
    return tuple(sorted(path.relative_to(root) for path in paths if path.is_file()))


def test_hybrid_branch_material_is_public_safe_and_excludes_lan() -> None:
    root = Path(__file__).resolve().parents[1]
    paths = _hybrid_paths(root)
    result = audit_public_candidate(root, paths)

    assert paths
    assert result.passed, result.to_dict()
    assert result.warning_count == 0, result.to_dict()

    forbidden_import = "ms8" + ".lan"
    forbidden_path = "src/ms8" + "/lan"
    for relative in paths:
        normalized = relative.as_posix().casefold()
        assert "/lan/" not in f"/{normalized}/"
        text = (root / relative).read_text(encoding="utf-8").casefold()
        assert forbidden_import not in text
        assert forbidden_path not in text


def test_internal_hybrid_planning_documents_are_not_public() -> None:
    root = Path(__file__).resolve().parents[1]

    for relative in _INTERNAL_DOCUMENTS:
        assert not (root / relative).exists(), relative


def test_public_hybrid_documents_exclude_internal_planning_markers() -> None:
    root = Path(__file__).resolve().parents[1]

    for relative in _PUBLIC_DOCUMENTS:
        path = root / relative
        assert path.is_file(), relative
        text = path.read_text(encoding="utf-8").casefold()
        for marker in _FORBIDDEN_PUBLIC_DOC_MARKERS:
            assert marker not in text, f"{relative}: {marker}"
