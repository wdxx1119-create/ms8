#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


@dataclass
class RuntimeLayout:
    root: Path
    score: int
    markers: dict[str, bool]


def _score_root(root: Path) -> RuntimeLayout:
    checks = {
        "records": root / "memory" / "auto_memory_records.jsonl",
        "kg_db": root / "memory" / "knowledge_graph.db",
        "memories_jsonl": root / "data" / "memories.jsonl",
        "auto_index": root / "memory" / "auto_memory_index.json",
        "memory_md": root / "MEMORY.md",
        "config_yaml": root / "config.yaml",
    }
    weights = {
        "records": 4,
        "kg_db": 3,
        "memories_jsonl": 2,
        "auto_index": 1,
        "memory_md": 1,
        "config_yaml": 1,
    }
    markers = {name: path.exists() for name, path in checks.items()}
    score = sum(weights[name] for name, ok in markers.items() if ok)
    return RuntimeLayout(root=root, score=score, markers=markers)


def _collect_files(root: Path) -> set[Path]:
    out: set[Path] = set()
    if not root.exists():
        return out
    for path in root.rglob("*"):
        if path.is_file():
            out.add(path)
    return out


def _relative(path: Path, base: Path) -> Path:
    return path.relative_to(base)


def build_plan(ms8: Path, legacy: Path, preferred: str = "auto") -> dict:
    a = _score_root(ms8)
    b = _score_root(legacy)

    if preferred == "ms8":
        primary, secondary = a, b
    elif preferred == "legacy":
        primary, secondary = b, a
    else:
        if a.score >= b.score:
            primary, secondary = a, b
        else:
            primary, secondary = b, a

    secondary_files = _collect_files(secondary.root)
    primary_files = _collect_files(primary.root)

    copy_items: list[dict] = []
    conflict_items: list[dict] = []

    for src in sorted(secondary_files):
        rel = _relative(src, secondary.root)
        dst = primary.root / rel
        if not dst.exists():
            copy_items.append({"src": str(src), "dst": str(dst), "rel": str(rel), "reason": "missing_in_primary"})
            continue
        try:
            same_size = src.stat().st_size == dst.stat().st_size
        except OSError:
            same_size = False
        if not same_size:
            conflict_items.append(
                {
                    "src": str(src),
                    "dst": str(dst),
                    "rel": str(rel),
                    "src_size": src.stat().st_size if src.exists() else None,
                    "dst_size": dst.stat().st_size if dst.exists() else None,
                }
            )

    # Also note files only in primary (for reporting)
    primary_only_count = 0
    for p in primary_files:
        rel = _relative(p, primary.root)
        if not (secondary.root / rel).exists():
            primary_only_count += 1

    return {
        "timestamp": _now(),
        "preferred": preferred,
        "primary": {"root": str(primary.root), "score": primary.score, "markers": primary.markers},
        "secondary": {"root": str(secondary.root), "score": secondary.score, "markers": secondary.markers},
        "copy_count": len(copy_items),
        "conflict_count": len(conflict_items),
        "primary_only_count": primary_only_count,
        "copy_items": copy_items,
        "conflict_items": conflict_items,
    }


def apply_plan(plan: dict, backup_root: Path) -> dict:
    primary_root = Path(plan["primary"]["root"])
    secondary_root = Path(plan["secondary"]["root"])
    stamp = plan["timestamp"]
    backup_dir = backup_root / f"runtime_converge_{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)

    applied = {"copied": 0, "backed_up": 0, "conflicts_archived": 0, "backup_dir": str(backup_dir)}

    # Copy missing files from secondary into primary
    for item in plan.get("copy_items", []):
        src = Path(item["src"])
        dst = Path(item["dst"])
        if not src.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        applied["copied"] += 1

    # Archive conflict files (secondary versions) for manual review
    conflict_archive = backup_dir / "conflicts_from_secondary"
    for item in plan.get("conflict_items", []):
        src = Path(item["src"])
        rel = Path(item["rel"])
        if not src.exists():
            continue
        arc = conflict_archive / rel
        arc.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, arc)
        applied["conflicts_archived"] += 1

    # Backup secondary full tree before decommission marker
    if secondary_root.exists():
        sec_backup = backup_dir / "secondary_snapshot"
        shutil.copytree(secondary_root, sec_backup, dirs_exist_ok=True)
        applied["backed_up"] = 1

    # Write migration marker in secondary root (non-destructive)
    marker = secondary_root / "MIGRATED_TO_MS8_HOME.txt"
    marker.write_text(
        (
            f"Migrated at {datetime.now(timezone.utc).isoformat()}\n"
            f"Primary runtime: {primary_root}\n"
            f"Backup: {backup_dir}\n"
            "This directory is kept for compatibility and can be removed after verification.\n"
        ),
        encoding="utf-8",
    )

    return applied


def main() -> int:
    parser = argparse.ArgumentParser(description="Converge ~/.ms8 and ~/.ms8_runtime into one authoritative runtime")
    parser.add_argument("--apply", action="store_true", help="apply migration plan (default is dry-run)")
    parser.add_argument(
        "--preferred",
        choices=["auto", "ms8", "legacy"],
        default="auto",
        help="force preferred primary runtime root",
    )
    args = parser.parse_args()

    home = Path.home()
    ms8 = home / ".ms8"
    legacy = home / ".ms8_runtime"
    plan = build_plan(ms8, legacy, preferred=args.preferred)
    print(json.dumps({"mode": "plan", **plan}, ensure_ascii=False, indent=2))

    if not args.apply:
        return 0

    backup_root = ms8 if ms8.exists() else legacy
    if not backup_root.exists():
        backup_root = home / ".ms8"
        backup_root.mkdir(parents=True, exist_ok=True)
    backup_root = backup_root / "backups"
    result = apply_plan(plan, backup_root)
    print(json.dumps({"mode": "apply", "result": result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
