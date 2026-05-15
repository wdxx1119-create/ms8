"""
Utility functions for the memory module.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path


def calculate_file_hash(file_path: Path) -> str:
    """Calculate a file SHA256 hash."""
    if not file_path.exists():
        return ""

    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as handle:
        for byte_block in iter(lambda: handle.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


def ensure_memory_directories(config: dict) -> None:
    """Ensure all required memory directories exist."""
    config["workspace_dir"].mkdir(parents=True, exist_ok=True)
    config["memory_dir"].mkdir(parents=True, exist_ok=True)

    memory_md = config["memory_md"]
    memory_md.parent.mkdir(parents=True, exist_ok=True)

    db_path = Path(config["settings"]["memory"]["long_term"]["path"])
    db_path.parent.mkdir(parents=True, exist_ok=True)

    index_dir = Path(config["settings"]["memory"]["keyword"]["index_dir"])
    index_dir.mkdir(parents=True, exist_ok=True)

    for relative_dir in ("subagents", "skills", ".skills", "skills/_bundled"):
        (config["memory_dir"] / relative_dir).mkdir(parents=True, exist_ok=True)

    (config["memory_dir"] / "archive").mkdir(parents=True, exist_ok=True)
    (config["memory_dir"] / "compression_reports").mkdir(parents=True, exist_ok=True)
    (config["memory_dir"] / "meta_reports").mkdir(parents=True, exist_ok=True)
    (config["memory_dir"] / "subagent_logs").mkdir(parents=True, exist_ok=True)
    config.get("daily_dir", (config["memory_dir"] / "daily")).mkdir(parents=True, exist_ok=True)


_DAILY_NAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[-_].+)?\.md$")


def is_daily_log_filename(name: str) -> bool:
    return bool(_DAILY_NAME_RE.match(str(name or "")))


def list_daily_log_files(memory_dir: Path, daily_dir: Path | None = None) -> list[Path]:
    """
    Return daily log files from the new layout (memory/daily/*.md) and keep
    legacy compatibility (memory/*.md date-named files).
    """
    files: list[Path] = []
    if daily_dir and daily_dir.exists():
        files.extend(sorted(daily_dir.glob("*.md")))
    legacy = []
    for path in sorted(memory_dir.glob("*.md")):
        if path.name == "MEMORY.md":
            continue
        if is_daily_log_filename(path.name):
            legacy.append(path)
    # Avoid duplicates by filename, prefer new daily_dir file.
    seen = {p.name for p in files}
    for path in legacy:
        if path.name not in seen:
            files.append(path)
    return files
