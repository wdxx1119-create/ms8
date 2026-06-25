"""Output generators for absorb project-memory."""

from __future__ import annotations

import ast
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .repository import active_chunks, active_files
from .scope import load_build_state, save_build_state

STOP_WORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "into",
    "uses",
    "using",
    "project",
    "memory",
    "file",
    "files",
}

MAX_FILE_INDEX_ROWS = 80
MAX_DIRECTORY_ROOTS = 24
MAX_HIGH_SIGNAL_FILES = 20
MAX_READING_ORDER = 16
MAX_CODE_MODULES = 40
MAX_RELATIONS = 100
MAX_RELATIONS_SUMMARY = 10
MAX_DOCUMENT_SECTIONS = 24
MAX_CHUNKS_PER_DOCUMENT = 3
MAX_CHUNK_TEXT_CHARS = 1200


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _snapshot_hash(files: list[dict[str, Any]]) -> str:
    joined = "|".join(
        f"{item.get('relative_path','')}::{item.get('content_hash','')}::{item.get('status','')}"
        for item in sorted(files, key=lambda row: str(row.get("relative_path", "")))
    )
    return hashlib.sha256(joined.encode("utf-8")).hexdigest() if joined else ""


def _relations_from_text(relative_path: str, text: str) -> list[dict[str, Any]]:
    patterns = [
        (re.compile(r"\b([A-Za-z][A-Za-z0-9_]+)\s+depends on\s+([A-Za-z][A-Za-z0-9_]+)", re.I), "depends_on", 0.72),
        (re.compile(r"\b([A-Za-z][A-Za-z0-9_]+)\s+uses\s+([A-Za-z][A-Za-z0-9_]+)", re.I), "uses", 0.78),
        (re.compile(r"\b([A-Za-z][A-Za-z0-9_]+)\s+is part of\s+([A-Za-z][A-Za-z0-9_]+)", re.I), "part_of", 0.68),
    ]
    out: list[dict[str, Any]] = []
    for line in text.splitlines():
        sentence = line.strip()
        if not sentence:
            continue
        for pattern, relation, confidence in patterns:
            match = pattern.search(sentence)
            if not match:
                continue
            out.append(
                {
                    "subject": match.group(1),
                    "relation": relation,
                    "object": match.group(2),
                    "source_file": relative_path,
                    "sentence": sentence,
                    "confidence": confidence,
                }
            )
    return out


def _extract_terms(chunks: list[dict[str, Any]], limit: int = 24) -> list[str]:
    counter: Counter[str] = Counter()
    for chunk in chunks:
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", str(chunk.get("text", ""))):
            lowered = token.lower()
            if lowered in STOP_WORDS:
                continue
            counter[lowered] += 1
    return [term for term, _ in counter.most_common(limit)]


def _directory_map(files: list[dict[str, Any]]) -> list[str]:
    tree: dict[str, set[str]] = defaultdict(set)
    roots: set[str] = set()
    for item in files:
        rel = str(item.get("relative_path", ""))
        parts = Path(rel).parts
        if not parts:
            continue
        roots.add(parts[0])
        if len(parts) == 1:
            tree["."].add(parts[0])
            continue
        for index in range(len(parts) - 1):
            parent = "/".join(parts[:index]) if index > 0 else "."
            child = parts[index]
            tree[parent].add(child)
        tree["/".join(parts[:-1]) if len(parts) > 1 else "."].add(parts[-1])
    lines: list[str] = []
    for root in sorted(roots)[:MAX_DIRECTORY_ROOTS]:
        lines.append(f"- {root}/")
        children = sorted(tree.get(root, set()))
        for child in children[:12]:
            lines.append(f"  - {child}")
    return lines or ["- (empty)"]


def _important_files(files: list[dict[str, Any]], grouped: dict[str, list[dict[str, Any]]], limit: int = 10) -> list[str]:
    scored: list[tuple[float, str]] = []
    for item in files:
        rel = str(item.get("relative_path", ""))
        name = Path(rel).name.lower()
        score = 0.0
        if name.startswith("readme"):
            score += 5
        if "config" in name or name.endswith((".toml", ".yaml", ".yml", ".json", ".ini", ".cfg")):
            score += 3
        if Path(rel).parts and Path(rel).parts[0] == "src":
            score += 2
        score += min(len(grouped.get(rel, [])), 6) * 0.4
        score += min(len(str(grouped.get(rel, [{}])[0].get("text_preview", ""))), 200) / 2000
        scored.append((score, rel))
    return [rel for _, rel in sorted(scored, key=lambda item: (-item[0], item[1]))[:limit]]


def _relation_summary(relations: list[dict[str, Any]], limit: int = 12) -> list[str]:
    if not relations:
        return ["- none"]
    counts: Counter[str] = Counter()
    for relation in relations:
        key = f"{relation['subject']} -> {relation['relation']} -> {relation['object']}"
        counts[key] += 1
    return [f"- {item}" for item, _count in counts.most_common(limit)]


def _reading_order(files: list[dict[str, Any]], grouped: dict[str, list[dict[str, Any]]], limit: int = 12) -> list[str]:
    priority_patterns = [
        "README",
        "docs/",
        "pyproject.toml",
        "requirements",
        "settings",
        "config",
        "src/",
        "app/",
        "tests/",
    ]
    ranked = _important_files(files, grouped, limit=max(limit, 20))
    ordered: list[str] = []
    used: set[str] = set()
    for pattern in priority_patterns:
        for rel in ranked:
            upper = rel.upper()
            if rel in used:
                continue
            if pattern.endswith("/") and rel.startswith(pattern):
                ordered.append(rel)
                used.add(rel)
            elif pattern.upper() in upper:
                ordered.append(rel)
                used.add(rel)
    for rel in ranked:
        if rel not in used:
            ordered.append(rel)
            used.add(rel)
    return ordered[:limit]


def _classify_python_role(
    relative_path: str,
    imports: list[str],
    classes: list[str],
    functions: list[str],
    module_doc: str,
) -> str:
    lower_path = relative_path.lower()
    lower_doc = module_doc.lower()
    names = {name.lower() for name in [*imports, *classes, *functions]}
    if "test" in lower_path or lower_path.startswith("tests/"):
        return "test"
    if lower_path.endswith(("cli.py", "__main__.py")):
        return "cli"
    if lower_path.endswith(("main.py", "app.py")):
        return "entrypoint"
    if "config" in lower_path or "settings" in lower_path:
        return "config"
    if "schema" in lower_path or any(name.endswith("schema") for name in names):
        return "schema"
    if "service" in lower_path or any(name.endswith("service") for name in names):
        return "service"
    if classes and functions:
        return "module_with_types"
    if classes:
        return "type_module"
    if functions:
        return "function_module"
    if "helper" in lower_path or "util" in lower_path or "support" in lower_doc:
        return "support"
    return "module"


def _analyze_python_file(path: Path, relative_path: str) -> dict[str, Any] | None:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None

    imports: list[str] = []
    classes: list[str] = []
    functions: list[str] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            imports.extend(f"{base}.{alias.name}" if base else alias.name for alias in node.names)
        elif isinstance(node, ast.ClassDef):
            classes.append(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(node.name)

    module_doc = ast.get_docstring(tree) or ""
    return {
        "path": relative_path,
        "module_role": _classify_python_role(relative_path, imports, classes, functions, module_doc),
        "imports": imports[:24],
        "classes": classes[:24],
        "functions": functions[:40],
        "module_doc": module_doc.strip(),
    }


def build_outputs(
    *,
    project_name: str,
    project_root: Path,
    db_path: Path,
    output_dir: Path,
    build_state_path: Path | None = None,
    changed_paths: list[str] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    files = active_files(db_path)
    chunks = active_chunks(db_path)
    snapshot_hash = _snapshot_hash(files)
    build_state_file = build_state_path or (output_dir.parent / "build_state.json")
    build_state = load_build_state(build_state_file)
    latest_seen = max((str(item.get("last_seen", "") or "") for item in files), default="")
    last_build_at = str(build_state.get("last_build_at", "") or "")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for chunk in chunks:
        grouped[str(chunk.get("relative_path", ""))].append(chunk)
    current_paths = {str(item.get("relative_path", "")) for item in files}
    changed_set = set(changed_paths or [])
    build_required = (
        bool(force)
        or str(build_state.get("snapshot_hash", "")) != snapshot_hash
        or (latest_seen > last_build_at)
    )
    refresh_all_entries = bool(force) or (
        str(build_state.get("snapshot_hash", "")) != snapshot_hash and not changed_set
    )
    if not build_required:
        ai_context = output_dir / "AI_CONTEXT.md"
        project_summary = output_dir / "project_summary.md"
        relations_path = output_dir / "relations.jsonl"
        reading_order_path = output_dir / "reading_order.json"
        hot_files_path = output_dir / "hot_files.json"
        code_index_path = output_dir / "code_index.json"
        outputs_ready = all(
            path.exists()
            for path in (
                ai_context,
                project_summary,
                relations_path,
                reading_order_path,
                hot_files_path,
                code_index_path,
            )
        )
        if outputs_ready:
            return {
                "ok": True,
                "name": project_name,
                "status": "up_to_date",
                "output": {
                    "ai_context": str(ai_context),
                    "project_summary": str(project_summary),
                    "relations": str(relations_path),
                    "reading_order": str(reading_order_path),
                    "hot_files": str(hot_files_path),
                    "code_index": str(code_index_path),
                },
                "stats": {
                    "files_included": len(files),
                    "chunks_included": len(chunks),
                    "relations_extracted": 0,
                    "reading_order_count": 0,
                    "hot_files_count": 0,
                    "python_modules_count": 0,
                    "document_sections_included": 0,
                    "ai_context_chars": ai_context.stat().st_size,
                    "project_summary_chars": project_summary.stat().st_size,
                    "rebuilt_file_entries": 0,
                    "reused_file_entries": len(files),
                },
            }

    cached_files = build_state.get("files", {})
    if not isinstance(cached_files, dict):
        cached_files = {}
    next_cached_files: dict[str, Any] = {}

    readme_summary = ""
    relations: list[dict[str, Any]] = []
    code_index: list[dict[str, Any]] = []
    rebuilt_entries = 0
    reused_entries = 0

    for item in files:
        rel = str(item.get("relative_path", ""))
        abs_path = str(item.get("absolute_path", ""))
        content_hash = str(item.get("content_hash", "") or "")
        previous_entry = cached_files.get(rel, {})
        if not isinstance(previous_entry, dict):
            previous_entry = {}
        can_reuse = (
            not refresh_all_entries
            and rel not in changed_set
            and str(previous_entry.get("content_hash", "")) == content_hash
        )
        if can_reuse:
            entry = dict(previous_entry)
            reused_entries += 1
        else:
            file_chunks = grouped.get(rel, [])
            entry = {
                "content_hash": content_hash,
                "text_preview": str(file_chunks[0].get("text_preview", ""))[:800] if file_chunks else "",
                "relations": [],
                "python_module": None,
            }
            if Path(rel).suffix.lower() in {".md", ".txt", ".rst"}:
                extracted_relations: list[dict[str, Any]] = []
                for chunk in file_chunks[:8]:
                    extracted_relations.extend(_relations_from_text(rel, str(chunk.get("text", ""))))
                entry["relations"] = extracted_relations
            if Path(rel).suffix.lower() == ".py" and abs_path:
                analyzed = _analyze_python_file(Path(abs_path), rel)
                if analyzed:
                    entry["python_module"] = analyzed
            rebuilt_entries += 1
        next_cached_files[rel] = entry
        if rel.upper().startswith("README") and not readme_summary:
            readme_summary = str(entry.get("text_preview", "") or "")[:800]
        relations.extend(list(entry.get("relations", []) or []))
        python_module = entry.get("python_module")
        if isinstance(python_module, dict):
            code_index.append(python_module)

    key_terms = _extract_terms(chunks)

    ai_context = output_dir / "AI_CONTEXT.md"
    project_summary = output_dir / "project_summary.md"
    relations_path = output_dir / "relations.jsonl"
    reading_order_path = output_dir / "reading_order.json"
    hot_files_path = output_dir / "hot_files.json"
    code_index_path = output_dir / "code_index.json"

    file_rows = []
    for index, item in enumerate(files, 1):
        rel = str(item.get("relative_path", ""))
        file_chunks = grouped.get(rel, [])
        summary = str(file_chunks[0].get("text_preview", ""))[:80].replace("\n", " ") if file_chunks else ""
        file_rows.append(f"| {index} | {rel} | {item.get('file_type','')} | {len(file_chunks)} | {summary} |")

    directory_map = _directory_map(files)
    hot_files = _important_files(files, grouped, limit=MAX_HIGH_SIGNAL_FILES)
    reading_order = _reading_order(files, grouped, limit=MAX_READING_ORDER)

    code_index = code_index[:MAX_CODE_MODULES]
    document_focus_files = hot_files[:MAX_DOCUMENT_SECTIONS]
    relation_lines = _relation_summary(relations, limit=MAX_RELATIONS_SUMMARY)

    ai_parts = [
        f"# Project Context: {project_name}",
        "",
        f"Generated: {_now()}",
        f"Source: {project_root}",
        f"Files: {len(files)}  |  Chunks: {len(chunks)}",
        "",
        "---",
        "",
        "## Project Overview",
        "",
        readme_summary or "No README summary available.",
        "",
        "---",
        "",
        "## File Index",
        "",
        "| # | File | Type | Chunks | Summary |",
        "|---|------|------|--------|---------|",
        *file_rows[:MAX_FILE_INDEX_ROWS],
        "",
        "---",
        "",
        "## Directory Map",
        "",
        *directory_map,
        "",
        "---",
        "",
        "## Recommended Reading Order",
        "",
        *[f"{idx}. {rel}" for idx, rel in enumerate(reading_order, 1)],
        "",
        "---",
        "",
        "## High-Signal Files",
        "",
        *[f"- {rel}" for rel in hot_files],
        "",
        "---",
        "",
        "## Python Code Map",
        "",
    ]
    if code_index:
        for module in code_index:
            ai_parts.append(f"### {module['path']}")
            ai_parts.append(f"- role: {module['module_role']}")
            if module["module_doc"]:
                ai_parts.append(f"- doc: {module['module_doc'][:180]}")
            if module["imports"]:
                ai_parts.append(f"- imports: {', '.join(module['imports'][:8])}")
            if module["classes"]:
                ai_parts.append(f"- classes: {', '.join(module['classes'][:8])}")
            if module["functions"]:
                ai_parts.append(f"- functions: {', '.join(module['functions'][:12])}")
            ai_parts.append("")
    else:
        ai_parts.extend(["No Python modules analyzed.", ""])
    ai_parts.extend(
        [
            "---",
            "",
            "## Focused Document Contents",
            "",
        ]
    )

    for rel in document_focus_files:
        file_chunks = grouped.get(rel, [])
        ai_parts.append(f"### {rel}")
        ai_parts.append(str(file_chunks[0].get("text_preview", ""))[:200] if file_chunks else "No parsed content.")
        ai_parts.append("")
        for chunk in file_chunks[:MAX_CHUNKS_PER_DOCUMENT]:
            ai_parts.append("<details>")
            ai_parts.append(f"<summary>Chunk {int(chunk.get('chunk_index', 0)) + 1} (tokens: {chunk.get('token_count', 0)})</summary>")
            ai_parts.append("")
            text = str(chunk.get("text", ""))
            ai_parts.append(text[:MAX_CHUNK_TEXT_CHARS])
            if len(text) > MAX_CHUNK_TEXT_CHARS:
                ai_parts.append("")
                ai_parts.append("[truncated]")
            ai_parts.append("")
            ai_parts.append("</details>")
            ai_parts.append("")
        ai_parts.append("---")
        ai_parts.append("")

    ai_parts.extend(
        [
            "## Detected Relations",
            "",
            "| Subject | Relation | Object | File |",
            "|---------|----------|--------|------|",
            *[f"| {r['subject']} | {r['relation']} | {r['object']} | {r['source_file']} |" for r in relations[:MAX_RELATIONS]],
            "",
            "---",
            "",
            "## Key Terms",
            "",
            ", ".join(key_terms) or "none",
            "",
        ]
    )
    ai_context.write_text("\n".join(ai_parts), encoding="utf-8")

    type_counts: Counter[str] = Counter(str(item.get("file_type", "")) for item in files)
    dir_tree = sorted({Path(str(item.get("relative_path", ""))).parts[0] for item in files if str(item.get("relative_path", ""))})
    summary_lines = [
        f"# Project Summary: {project_name}",
        "",
        f"Scan time: {_now()}",
        f"Project root: {project_root}",
        "",
        "## Stats",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Files scanned | {len(files)} |",
        f"| Total chunks | {len(chunks)} |",
        f"| Total tokens (est.) | ~{sum(int(chunk.get('token_count', 0) or 0) for chunk in chunks)} |",
        "",
        "## File Type Distribution",
        "",
        "| Type | Count |",
        "|------|-------|",
        *[f"| {suffix} | {count} |" for suffix, count in sorted(type_counts.items())],
        "",
        "## Directory Roots",
        "",
        *[f"- {part}/" for part in dir_tree],
        "",
        "## Recommended Reading Order",
        "",
        *[f"{idx}. {rel}" for idx, rel in enumerate(reading_order, 1)],
        "",
        "## High-Signal Files",
        "",
        *[f"- {rel}" for rel in hot_files],
        "",
        "## Relation Summary",
        "",
        *relation_lines,
        "",
        "## Python Modules",
        "",
    ]
    if code_index:
        summary_lines.extend([f"- {module['path']} ({module['module_role']})" for module in code_index[:20]])
    else:
        summary_lines.append("- none")
    summary_lines.extend(
        [
            "",
            "## Top Relations Found",
            "",
            *[f"{idx}. {r['subject']} -> {r['relation']} -> {r['object']}" for idx, r in enumerate(relations[:MAX_RELATIONS_SUMMARY], 1)],
            "",
        ]
    )
    project_summary.write_text("\n".join(summary_lines), encoding="utf-8")

    with relations_path.open("w", encoding="utf-8") as fh:
        for relation in relations:
            fh.write(json.dumps(relation, ensure_ascii=False) + "\n")
    reading_order_path.write_text(
        json.dumps({"project": project_name, "reading_order": reading_order}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    hot_files_path.write_text(
        json.dumps({"project": project_name, "hot_files": hot_files}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    code_index_path.write_text(
        json.dumps({"project": project_name, "modules": code_index}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    next_build_state = {
        "version": 1,
        "snapshot_hash": snapshot_hash,
        "last_build_at": _now(),
        "last_error": "",
        "files": {rel: payload for rel, payload in next_cached_files.items() if rel in current_paths},
    }
    save_build_state(build_state_file, next_build_state)

    return {
        "ok": True,
        "name": project_name,
        "status": "built",
        "output": {
            "ai_context": str(ai_context),
            "project_summary": str(project_summary),
            "relations": str(relations_path),
            "reading_order": str(reading_order_path),
            "hot_files": str(hot_files_path),
            "code_index": str(code_index_path),
        },
        "stats": {
            "files_included": len(files),
            "chunks_included": len(chunks),
            "relations_extracted": len(relations),
            "reading_order_count": len(reading_order),
            "hot_files_count": len(hot_files),
            "python_modules_count": len(code_index),
            "document_sections_included": len(document_focus_files),
            "ai_context_chars": ai_context.stat().st_size,
            "project_summary_chars": project_summary.stat().st_size,
            "rebuilt_file_entries": rebuilt_entries,
            "reused_file_entries": reused_entries,
        },
    }
