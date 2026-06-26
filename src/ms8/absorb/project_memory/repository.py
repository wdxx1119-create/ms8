"""SQLite repository for absorb project-memory."""

from __future__ import annotations

import hashlib
import sqlite3
from collections import Counter
from collections.abc import Iterable
from contextlib import contextmanager
from pathlib import Path
from typing import Any


def _connect(db: Path) -> sqlite3.Connection:
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


@contextmanager
def connection(db: Path):
    conn = _connect(db)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_repository(db: Path) -> None:
    with connection(db) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS file_records (
                file_id TEXT PRIMARY KEY,
                relative_path TEXT UNIQUE,
                absolute_path TEXT,
                file_type TEXT,
                title TEXT,
                size INTEGER,
                mtime REAL,
                content_hash TEXT,
                status TEXT DEFAULT 'SCANNED',
                parse_status TEXT DEFAULT 'ok',
                first_seen TEXT,
                last_seen TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chunks (
                chunk_id TEXT PRIMARY KEY,
                file_id TEXT,
                chunk_index INTEGER,
                chunk_hash TEXT,
                text_preview TEXT,
                token_count INTEGER,
                text TEXT,
                status TEXT DEFAULT 'ACTIVE'
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_file ON chunks(file_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_files_status ON file_records(status)")


def _file_id(relative_path: str) -> str:
    return "file_" + hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:24]


def _chunk_id(file_id: str, chunk_index: int, chunk_hash: str) -> str:
    return f"chunk_{file_id}_{chunk_index}_{chunk_hash[:12]}"


def upsert_file(conn: sqlite3.Connection, *, relative_path: str, absolute_path: str, file_type: str, title: str, size: int, mtime: float, content_hash: str, parse_status: str, status: str = "INDEXED") -> str:
    file_id = _file_id(relative_path)
    row = conn.execute("SELECT first_seen FROM file_records WHERE relative_path=?", (relative_path,)).fetchone()
    first_seen = row["first_seen"] if row else None
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO file_records (
            file_id, relative_path, absolute_path, file_type, title, size, mtime,
            content_hash, status, parse_status, first_seen, last_seen
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(relative_path) DO UPDATE SET
            absolute_path=excluded.absolute_path,
            file_type=excluded.file_type,
            title=excluded.title,
            size=excluded.size,
            mtime=excluded.mtime,
            content_hash=excluded.content_hash,
            status=excluded.status,
            parse_status=excluded.parse_status,
            last_seen=excluded.last_seen
        """,
        (
            file_id,
            relative_path,
            absolute_path,
            file_type,
            title,
            size,
            mtime,
            content_hash,
            status,
            parse_status,
            first_seen or now,
            now,
        ),
    )
    return file_id


def replace_chunks(conn: sqlite3.Connection, file_id: str, chunks: Iterable[dict[str, Any]]) -> int:
    conn.execute("DELETE FROM chunks WHERE file_id=?", (file_id,))
    count = 0
    for chunk in chunks:
        chunk_hash = str(chunk["chunk_hash"])
        chunk_index = int(chunk["chunk_index"])
        conn.execute(
            """
            INSERT INTO chunks (chunk_id, file_id, chunk_index, chunk_hash, text_preview, token_count, text, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'ACTIVE')
            """,
            (
                _chunk_id(file_id, chunk_index, chunk_hash),
                file_id,
                chunk_index,
                chunk_hash,
                str(chunk["text"])[:500],
                int(chunk["token_count"]),
                str(chunk["text"]),
            ),
        )
        count += 1
    return count


def mark_deleted_missing(conn: sqlite3.Connection, seen_relative_paths: set[str]) -> int:
    rows = conn.execute("SELECT relative_path, status FROM file_records").fetchall()
    changed = 0
    for row in rows:
        rel = str(row["relative_path"])
        if rel not in seen_relative_paths and str(row["status"]) != "DELETED":
            conn.execute("UPDATE file_records SET status='DELETED' WHERE relative_path=?", (rel,))
            changed += 1
    return changed


def stats(db: Path) -> dict[str, Any]:
    init_repository(db)
    with connection(db) as conn:
        file_count = int(conn.execute("SELECT COUNT(*) FROM file_records WHERE status != 'DELETED'").fetchone()[0])
        chunk_count = int(conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
        file_types = Counter()
        for row in conn.execute("SELECT file_type FROM file_records WHERE status != 'DELETED'"):
            file_types[str(row["file_type"] or "")] += 1
        last_scan_at = conn.execute("SELECT MAX(last_seen) FROM file_records").fetchone()[0] or ""
    return {
        "file_count": file_count,
        "chunk_count": chunk_count,
        "file_types": dict(sorted(file_types.items())),
        "last_scan_at": str(last_scan_at),
    }


def active_files(db: Path) -> list[dict[str, Any]]:
    init_repository(db)
    with connection(db) as conn:
        rows = conn.execute(
            """
            SELECT file_id, relative_path, absolute_path, file_type, title, size, mtime, content_hash, status, parse_status, last_seen
            FROM file_records
            WHERE status != 'DELETED'
            ORDER BY relative_path
            """
        ).fetchall()
    return [dict(row) for row in rows]


def active_chunks(db: Path) -> list[dict[str, Any]]:
    init_repository(db)
    with connection(db) as conn:
        rows = conn.execute(
            """
            SELECT c.chunk_id, c.file_id, c.chunk_index, c.chunk_hash, c.text_preview, c.token_count, c.text, c.status,
                   f.relative_path, f.file_type, f.title
            FROM chunks c
            JOIN file_records f ON f.file_id = c.file_id
            WHERE c.status='ACTIVE' AND f.status != 'DELETED'
            ORDER BY f.relative_path, c.chunk_index
            """
        ).fetchall()
    return [dict(row) for row in rows]


def active_chunks_for_paths(db: Path, relative_paths: list[str]) -> list[dict[str, Any]]:
    if not relative_paths:
        return []
    init_repository(db)
    placeholders = ",".join("?" for _ in relative_paths)
    with connection(db) as conn:
        rows = conn.execute(
            f"""
            SELECT c.chunk_id, c.file_id, c.chunk_index, c.chunk_hash, c.text_preview, c.token_count, c.text, c.status,
                   f.relative_path, f.file_type, f.title
            FROM chunks c
            JOIN file_records f ON f.file_id = c.file_id
            WHERE c.status='ACTIVE' AND f.status != 'DELETED' AND f.relative_path IN ({placeholders})
            ORDER BY f.relative_path, c.chunk_index
            """,
            tuple(relative_paths),
        ).fetchall()
    return [dict(row) for row in rows]


def get_chunk_by_id(db: Path, chunk_id: str) -> dict[str, Any] | None:
    with connection(db) as conn:
        row = conn.execute(
            """
            SELECT c.chunk_id, c.file_id, c.chunk_index, c.text_preview, c.token_count, c.text, f.relative_path, f.file_type
            FROM chunks c JOIN file_records f ON f.file_id = c.file_id
            WHERE c.chunk_id=?
            """,
            (chunk_id,),
        ).fetchone()
    return dict(row) if row else None


def sqlite_search(db: Path, query: str, limit: int = 10) -> list[dict[str, Any]]:
    q = f"%{query.strip()}%"
    if q == "%%":
        return []
    with connection(db) as conn:
        rows = conn.execute(
            """
            SELECT c.chunk_id, c.file_id, c.chunk_index, c.text_preview, c.token_count, c.text,
                   f.relative_path, f.file_type
            FROM chunks c
            JOIN file_records f ON f.file_id = c.file_id
            WHERE c.status='ACTIVE' AND f.status != 'DELETED'
              AND (c.text LIKE ? OR f.relative_path LIKE ?)
            LIMIT ?
            """,
            (q, q, int(limit)),
        ).fetchall()
    return [dict(row) | {"search_backend": "sqlite"} for row in rows]
