from __future__ import annotations

from pathlib import Path

from ms8.memory.application.public_safety import audit_public_candidate


def _hybrid_paths(root: Path) -> tuple[Path, ...]:
    paths: list[Path] = []
    paths.extend((root / "src/ms8/memory/retrieval").rglob("*.py"))
    paths.extend((root / "tests").glob("test_memory_hybrid_*.py"))
    paths.append(root / "docs/development/MEMORY_HYBRID_V1_TASKS.md")
    return tuple(sorted(path.relative_to(root) for path in paths if path.is_file()))


def test_hybrid_branch_material_is_public_safe_and_excludes_lan() -> None:
    root = Path(__file__).resolve().parents[1]
    paths = _hybrid_paths(root)
    result = audit_public_candidate(root, paths)

    assert paths
    assert result.passed, result.to_dict()
    assert result.warning_count == 0, result.to_dict()

    for relative in paths:
        normalized = relative.as_posix().casefold()
        assert "/lan/" not in f"/{normalized}/"
        text = (root / relative).read_text(encoding="utf-8").casefold()
        assert "ms8.lan" not in text
        assert "src/ms8/lan" not in text
