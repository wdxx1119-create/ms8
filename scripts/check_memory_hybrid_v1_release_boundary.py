from __future__ import annotations

import argparse
import json
import re
import tarfile
import zipfile
from pathlib import Path

OPTIONAL_RETRIEVAL_DEPENDENCIES = frozenset(
    {
        "faiss-cpu",
        "faiss-gpu",
        "hnswlib",
        "lightgbm",
        "ollama",
        "rank-bm25",
        "scikit-learn",
        "sentence-transformers",
        "torch",
        "transformers",
        "xgboost",
    }
)
_FORBIDDEN_FILE_NAMES = frozenset({".env", "id_dsa", "id_ecdsa", "id_ed25519", "id_rsa"})
_FORBIDDEN_SUFFIXES = (".key", ".p12", ".pem", ".pfx")
_FORBIDDEN_COMPONENTS = frozenset(
    {
        "credentials",
        "private_assets",
        "private_fixtures",
        "secrets",
    }
)
_REQUIREMENT_NAME_BOUNDARY = re.compile(r"[<>=!~\s;(\[]")


def _normalized_members(names: list[str]) -> tuple[str, ...]:
    return tuple(sorted({name.replace("\\", "/").lstrip("./") for name in names if name}))


def _wheel_members(path: Path) -> tuple[str, ...]:
    with zipfile.ZipFile(path) as archive:
        return _normalized_members(archive.namelist())


def _sdist_members(path: Path) -> tuple[str, ...]:
    with tarfile.open(path, "r:gz") as archive:
        return _normalized_members(member.name for member in archive.getmembers())


def _forbidden_members(members: tuple[str, ...]) -> tuple[str, ...]:
    forbidden: list[str] = []
    for member in members:
        lowered = member.casefold()
        parts = tuple(part for part in lowered.split("/") if part)
        name = parts[-1] if parts else ""
        if "ms8" in parts:
            index = parts.index("ms8")
            if index + 1 < len(parts) and parts[index + 1] == "lan":
                forbidden.append(member)
                continue
        if _FORBIDDEN_COMPONENTS.intersection(parts):
            forbidden.append(member)
            continue
        if name in _FORBIDDEN_FILE_NAMES or name.endswith(_FORBIDDEN_SUFFIXES):
            forbidden.append(member)
    return tuple(sorted(forbidden))


def _metadata_text(wheel: Path) -> str:
    with zipfile.ZipFile(wheel) as archive:
        matches = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        if len(matches) != 1:
            raise RuntimeError(f"wheel must contain exactly one METADATA file, found {len(matches)}")
        return archive.read(matches[0]).decode("utf-8")


def _requirement_name(raw: str) -> str:
    token = _REQUIREMENT_NAME_BOUNDARY.split(raw.strip(), maxsplit=1)[0]
    return token.replace("_", "-").casefold()


def _unconditional_optional_dependencies(metadata: str) -> tuple[str, ...]:
    violations: list[str] = []
    for line in metadata.splitlines():
        if not line.startswith("Requires-Dist:"):
            continue
        requirement = line.partition(":")[2].strip()
        if _requirement_name(requirement) not in OPTIONAL_RETRIEVAL_DEPENDENCIES:
            continue
        if "extra ==" not in requirement.casefold():
            violations.append(requirement)
    return tuple(sorted(violations))


def inspect_release_boundary(wheel: Path, sdist: Path) -> dict[str, object]:
    wheel = Path(wheel)
    sdist = Path(sdist)
    if not wheel.is_file():
        raise FileNotFoundError(wheel)
    if not sdist.is_file():
        raise FileNotFoundError(sdist)

    wheel_members = _wheel_members(wheel)
    sdist_members = _sdist_members(sdist)
    forbidden = tuple(sorted(set(_forbidden_members(wheel_members) + _forbidden_members(sdist_members))))
    unconditional = _unconditional_optional_dependencies(_metadata_text(wheel))
    return {
        "schema": "ms8.hybrid_release_boundary.v1",
        "passed": not forbidden and not unconditional,
        "wheel": wheel.name,
        "sdist": sdist.name,
        "wheel_member_count": len(wheel_members),
        "sdist_member_count": len(sdist_members),
        "forbidden_members": list(forbidden),
        "unconditional_optional_retrieval_dependencies": list(unconditional),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify Hybrid Retrieval v1 release boundaries")
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--sdist", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    report = inspect_release_boundary(args.wheel, args.sdist)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if bool(report["passed"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
