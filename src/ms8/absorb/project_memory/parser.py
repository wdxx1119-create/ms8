"""Parser wrappers for absorb project-memory."""

from __future__ import annotations

import configparser
import hashlib
import json
import tomllib
from pathlib import Path

from ..parser import ParsedDocument, parse_document as absorb_parse_document


def _hash_text(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def parse_document(path: str | Path) -> ParsedDocument:
    p = Path(path).expanduser().resolve()
    suffix = p.suffix.lower()
    if suffix == ".rst":
        text = p.read_text(encoding="utf-8", errors="replace")
        return ParsedDocument(
            source_path=str(p),
            file_type=suffix,
            title=p.stem,
            content_text=text,
            content_hash=_hash_text(text),
            metadata={"size": p.stat().st_size, "mtime": p.stat().st_mtime},
            parse_status="parsed" if text.strip() else "empty",
        )
    if suffix == ".toml":
        raw = p.read_bytes()
        payload = tomllib.loads(raw.decode("utf-8", errors="replace"))
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        return ParsedDocument(
            source_path=str(p),
            file_type=suffix,
            title=p.stem,
            content_text=text,
            content_hash=_hash_text(text),
            metadata={"size": p.stat().st_size, "mtime": p.stat().st_mtime},
            parse_status="parsed" if text.strip() else "empty",
        )
    if suffix in {".cfg", ".ini"}:
        parser = configparser.ConfigParser()
        parser.read(p, encoding="utf-8")
        lines: list[str] = []
        for section in parser.sections():
            lines.append(f"[{section}]")
            for key, value in parser.items(section):
                lines.append(f"{key} = {value}")
        text = "\n".join(lines)
        return ParsedDocument(
            source_path=str(p),
            file_type=suffix,
            title=p.stem,
            content_text=text,
            content_hash=_hash_text(text),
            metadata={"size": p.stat().st_size, "mtime": p.stat().st_mtime},
            parse_status="parsed" if text.strip() else "empty",
        )
    return absorb_parse_document(p)
