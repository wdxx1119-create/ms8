from __future__ import annotations

import importlib.util
import io
import tarfile
import zipfile
from pathlib import Path
from types import ModuleType


def _load_checker() -> ModuleType:
    root = Path(__file__).resolve().parents[1]
    path = root / "scripts" / "check_memory_hybrid_v1_release_boundary.py"
    spec = importlib.util.spec_from_file_location("memory_hybrid_release_boundary", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _artifacts(
    tmp_path: Path,
    *,
    requirements: tuple[str, ...] = (),
    wheel_members: tuple[str, ...] = (),
    sdist_members: tuple[str, ...] = (),
) -> tuple[Path, Path]:
    wheel = tmp_path / "ms8-0.2.18-py3-none-any.whl"
    metadata = [
        "Metadata-Version: 2.4",
        "Name: ms8",
        "Version: 0.2.18",
        *(f"Requires-Dist: {value}" for value in requirements),
        "",
    ]
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("ms8-0.2.18.dist-info/METADATA", "\n".join(metadata))
        archive.writestr("ms8/__init__.py", "__version__ = '0.2.18'\n")
        for member in wheel_members:
            archive.writestr(member, "fixture\n")

    sdist = tmp_path / "ms8-0.2.18.tar.gz"
    with tarfile.open(sdist, "w:gz") as archive:
        for member in ("ms8-0.2.18/pyproject.toml", *sdist_members):
            payload = b"fixture\n"
            info = tarfile.TarInfo(member)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return wheel, sdist


def test_release_boundary_allows_optional_extra_dependencies(tmp_path: Path) -> None:
    checker = _load_checker()
    wheel, sdist = _artifacts(
        tmp_path,
        requirements=(
            "requests>=2.31.0",
            'ollama>=0.4.0; extra == "llm"',
            'sentence-transformers>=3.0; extra == "retrieval"',
        ),
    )

    report = checker.inspect_release_boundary(wheel, sdist)

    assert report["passed"] is True
    assert report["forbidden_members"] == []
    assert report["unconditional_optional_retrieval_dependencies"] == []


def test_release_boundary_rejects_unconditional_embedding_or_ltr_dependency(tmp_path: Path) -> None:
    checker = _load_checker()
    wheel, sdist = _artifacts(
        tmp_path,
        requirements=("hnswlib>=0.8.0", "LightGBM~=4.6"),
    )

    report = checker.inspect_release_boundary(wheel, sdist)

    assert report["passed"] is False
    assert report["unconditional_optional_retrieval_dependencies"] == [
        "LightGBM~=4.6",
        "hnswlib>=0.8.0",
    ]


def test_release_boundary_rejects_lan_and_private_key_material(tmp_path: Path) -> None:
    checker = _load_checker()
    wheel, sdist = _artifacts(
        tmp_path,
        wheel_members=("ms8/lan/server.py",),
        sdist_members=("ms8-0.2.18/private_assets/release.pem",),
    )

    report = checker.inspect_release_boundary(wheel, sdist)

    assert report["passed"] is False
    assert report["forbidden_members"] == [
        "ms8-0.2.18/private_assets/release.pem",
        "ms8/lan/server.py",
    ]
