from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional


@dataclass
class MemorySection:
    title: str
    body: str
    section_type: str
    line_count: int
    char_count: int
    estimated_date: Optional[str]
    similarity_key: str


def _classify_section_type(title: str) -> str:
    low = title.lower()
    if "learning summary" in low:
        return "learning_summary"
    if "verification" in low:
        return "verification"
    if "smoke" in low:
        return "smoke_test"
    if any(k in low for k in ["research", "研究"]):
        return "research"
    if any(k in low for k in ["story", "故事", "剧情"]):
        return "storyline"
    if any(k in low for k in ["decision", "决定"]):
        return "decision"
    return "unknown"


def _extract_date(title: str, body: str) -> Optional[str]:
    m = re.search(r"(\d{4}-\d{2}-\d{2})", f"{title}\n{body}")
    if not m:
        return None
    try:
        datetime.fromisoformat(m.group(1))
        return m.group(1)
    except Exception:
        return None


def parse_memory_sections(memory_text: str) -> List[Dict[str, object]]:
    lines = str(memory_text or "").splitlines()
    sections: List[MemorySection] = []

    current_title = "Preamble"
    current_body: List[str] = []

    def _flush():
        title = current_title.strip()
        body = "\n".join(current_body).strip()
        body_norm = re.sub(r"\s+", " ", body.lower())
        section_type = _classify_section_type(title)
        similarity_key = hashlib.sha1(f"{section_type}:{title.lower()}:{body_norm}".encode("utf-8")).hexdigest()[:16]
        sec = MemorySection(
            title=title,
            body=body,
            section_type=section_type,
            line_count=len(body.splitlines()) if body else 0,
            char_count=len(body),
            estimated_date=_extract_date(title, body),
            similarity_key=similarity_key,
        )
        sections.append(sec)

    for line in lines:
        if line.startswith("## "):
            _flush()
            current_title = line[3:].strip()
            current_body = []
        else:
            current_body.append(line)
    _flush()

    out: List[Dict[str, object]] = []
    for s in sections:
        if s.title == "Preamble" and not s.body:
            continue
        out.append(
            {
                "title": s.title,
                "body": s.body,
                "section_type": s.section_type,
                "line_count": s.line_count,
                "char_count": s.char_count,
                "estimated_date": s.estimated_date,
                "similarity_key": s.similarity_key,
            }
        )
    return out
