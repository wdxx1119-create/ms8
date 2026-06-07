"""SQLite repository for authorized local document absorption."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..paths import get_ms8_home

EVENTS_WARN_BYTES = 50 * 1024 * 1024
EVENTS_DEGRADED_BYTES = 200 * 1024 * 1024
EVENTS_ROTATE_BYTES = 10 * 1024 * 1024
EVENTS_ROTATE_KEEP = 5

STATUSES = {
    "DISCOVERED",
    "LOCAL_INDEXED",
    "DUPLICATE",
    "CHANGED",
    "READY_FOR_PARSE",
    "PARSED",
    "READY_FOR_GOVERNANCE",
    "PENDING_REVIEW",
    "QUARANTINED",
    "READY_FOR_MS8",
    "SUBMITTED_TO_MS8",
    "MS8_ACCEPTED",
    "MS8_REJECTED",
    "DELETED",
    "ERROR",
    "FILTERED",
    "OCR_REQUIRED",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def absorb_root() -> Path:
    return get_ms8_home() / "absorb"


def db_path() -> Path:
    return absorb_root() / "absorb.sqlite"


def events_path() -> Path:
    return absorb_root() / "events.jsonl"


def quarantine_dir() -> Path:
    return absorb_root() / "quarantine"


def _rotated_events_path(index: int) -> Path:
    return absorb_root() / f"events.{index}.jsonl"


def _rotate_events_file_if_needed(max_bytes: int = EVENTS_ROTATE_BYTES, keep: int = EVENTS_ROTATE_KEEP) -> None:
    event_file = events_path()
    try:
        if not event_file.exists() or event_file.stat().st_size < max(1, int(max_bytes)):
            return
        max_keep = max(1, int(keep))
        oldest = _rotated_events_path(max_keep)
        oldest.unlink(missing_ok=True)
        for index in range(max_keep - 1, 0, -1):
            src = _rotated_events_path(index)
            if src.exists():
                src.replace(_rotated_events_path(index + 1))
        event_file.replace(_rotated_events_path(1))
    except OSError:
        # Rotation is best-effort; append path should remain available.
        return


def path_hash(path: str | Path) -> str:
    return hashlib.sha256(str(path).encode("utf-8")).hexdigest()


def init_repository(path: Path | None = None) -> Path:
    db = path or db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    quarantine_dir().mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS file_records (
                file_id TEXT PRIMARY KEY,
                canonical_path TEXT UNIQUE,
                path_hash TEXT,
                content_hash TEXT,
                quick_hash TEXT,
                file_type TEXT,
                size INTEGER,
                mtime REAL,
                ctime REAL,
                first_seen_at TEXT,
                last_seen_at TEXT,
                status TEXT,
                risk_level TEXT,
                parse_status TEXT,
                source TEXT,
                ms8_record_id TEXT,
                error TEXT
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
                status TEXT,
                risk_level TEXT,
                submitted_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ingest_jobs (
                job_id TEXT PRIMARY KEY,
                file_id TEXT,
                job_type TEXT,
                status TEXT,
                reason TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT,
                file_id TEXT,
                path TEXT,
                decision TEXT,
                reason TEXT,
                created_at TEXT
            )
            """
        )
    return db


def _connect() -> sqlite3.Connection:
    init_repository()
    conn = sqlite3.connect(db_path())
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def _connection() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _file_id(canonical_path: str) -> str:
    return "file_" + path_hash(canonical_path)[:24]


def _job_id(file_id: str, job_type: str) -> str:
    return "job_" + hashlib.sha256(f"{file_id}:{job_type}:{_now()}".encode()).hexdigest()[:24]


def _event_id(event_type: str, path: str) -> str:
    return "evt_" + hashlib.sha256(f"{event_type}:{path}:{_now()}".encode()).hexdigest()[:24]


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def upsert_file_record(
    *,
    canonical_path: str,
    file_type: str = "",
    size: int = 0,
    mtime: float = 0.0,
    ctime: float = 0.0,
    content_hash: str = "",
    quick_hash: str = "",
    status: str = "DISCOVERED",
    risk_level: str = "unknown",
    parse_status: str = "pending",
    source: str = "absorb",
    error: str = "",
) -> dict[str, Any]:
    now = _now()
    file_id = _file_id(canonical_path)
    with _connection() as conn:
        existing = conn.execute("SELECT first_seen_at FROM file_records WHERE canonical_path=?", (canonical_path,)).fetchone()
        first_seen = existing["first_seen_at"] if existing else now
        conn.execute(
            """
            INSERT INTO file_records (
                file_id, canonical_path, path_hash, content_hash, quick_hash, file_type, size, mtime, ctime,
                first_seen_at, last_seen_at, status, risk_level, parse_status, source, ms8_record_id, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(canonical_path) DO UPDATE SET
                content_hash=excluded.content_hash,
                quick_hash=excluded.quick_hash,
                file_type=excluded.file_type,
                size=excluded.size,
                mtime=excluded.mtime,
                ctime=excluded.ctime,
                last_seen_at=excluded.last_seen_at,
                status=excluded.status,
                risk_level=excluded.risk_level,
                parse_status=excluded.parse_status,
                source=excluded.source,
                error=excluded.error
            """,
            (
                file_id,
                canonical_path,
                path_hash(canonical_path),
                content_hash,
                quick_hash,
                file_type,
                size,
                mtime,
                ctime,
                first_seen,
                now,
                status if status in STATUSES else "DISCOVERED",
                risk_level,
                parse_status,
                source,
                "",
                error,
            ),
        )
        row = conn.execute("SELECT * FROM file_records WHERE canonical_path=?", (canonical_path,)).fetchone()
    return dict(row)


def get_file_by_path(path: str | Path) -> dict[str, Any] | None:
    canonical = str(Path(path).expanduser().resolve())
    with _connection() as conn:
        return _row_to_dict(conn.execute("SELECT * FROM file_records WHERE canonical_path=?", (canonical,)).fetchone())


def get_file_by_hash(content_hash: str) -> dict[str, Any] | None:
    with _connection() as conn:
        return _row_to_dict(conn.execute("SELECT * FROM file_records WHERE content_hash=?", (content_hash,)).fetchone())


def update_file_status(file_id: str, status: str, reason: str | None = None) -> None:
    with _connection() as conn:
        conn.execute(
            "UPDATE file_records SET status=?, error=?, last_seen_at=? WHERE file_id=?",
            (status if status in STATUSES else "ERROR", reason or "", _now(), file_id),
        )


def update_chunk_status(chunk_id: str, status: str, *, submitted: bool = False) -> None:
    submitted_at = _now() if submitted else ""
    with _connection() as conn:
        conn.execute(
            "UPDATE chunks SET status=?, submitted_at=? WHERE chunk_id=?",
            (status, submitted_at, chunk_id),
        )


def get_chunk(chunk_id: str) -> dict[str, Any] | None:
    with _connection() as conn:
        row = conn.execute(
            """
            SELECT c.*, f.canonical_path, f.file_type, f.status AS file_status
            FROM chunks c JOIN file_records f ON c.file_id = f.file_id
            WHERE c.chunk_id=?
            """,
            (chunk_id,),
        ).fetchone()
        return _row_to_dict(row)


def add_ingest_job(file_id: str, job_type: str, reason: str | None = None) -> dict[str, Any]:
    now = _now()
    row = {
        "job_id": _job_id(file_id, job_type),
        "file_id": file_id,
        "job_type": job_type,
        "status": "pending",
        "reason": reason or "",
        "created_at": now,
        "updated_at": now,
    }
    with _connection() as conn:
        conn.execute(
            "INSERT INTO ingest_jobs VALUES (?, ?, ?, ?, ?, ?, ?)",
            tuple(row.values()),
        )
    return row


def add_chunk(
    *,
    file_id: str,
    chunk_index: int,
    chunk_hash: str,
    text_preview: str,
    token_count: int,
    status: str = "LOCAL_INDEXED",
    risk_level: str = "low",
) -> dict[str, Any]:
    chunk_id = f"chunk_{file_id}_{chunk_index}_{chunk_hash[:12]}"
    row = {
        "chunk_id": chunk_id,
        "file_id": file_id,
        "chunk_index": chunk_index,
        "chunk_hash": chunk_hash,
        "text_preview": text_preview[:500],
        "token_count": token_count,
        "status": status,
        "risk_level": risk_level,
        "submitted_at": "",
    }
    with _connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO chunks (
                chunk_id, file_id, chunk_index, chunk_hash, text_preview, token_count, status, risk_level, submitted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            tuple(row.values()),
        )
    return row


def log_event(event_type: str, path: str, decision: str, reason: str | None = None, file_id: str = "") -> dict[str, Any]:
    row = {
        "event_id": _event_id(event_type, path),
        "event_type": event_type,
        "file_id": file_id,
        "path": path,
        "decision": decision,
        "reason": reason or "",
        "created_at": _now(),
    }
    absorb_root().mkdir(parents=True, exist_ok=True)
    _rotate_events_file_if_needed()
    with events_path().open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    with _connection() as conn:
        conn.execute(
            "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?, ?)",
            tuple(row.values()),
        )
    return row


def list_audit_events(event_type: str = "", decision: str = "", reason_contains: str = "", limit: int = 100) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if event_type:
        clauses.append("event_type=?")
        params.append(event_type)
    if decision:
        clauses.append("decision=?")
        params.append(decision)
    if reason_contains:
        clauses.append("reason LIKE ?")
        params.append(f"%{reason_contains}%")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM audit_events {where} ORDER BY created_at DESC LIMIT ?",
            (*params, int(limit)),
        )
        return [dict(row) for row in rows]


def count_status() -> dict[str, Any]:
    with _connection() as conn:
        files = {row["status"]: row["n"] for row in conn.execute("SELECT status, COUNT(*) AS n FROM file_records GROUP BY status")}
        chunks = {row["status"]: row["n"] for row in conn.execute("SELECT status, COUNT(*) AS n FROM chunks GROUP BY status")}
    return {"files": files, "chunks": chunks}


def repository_integrity() -> dict[str, Any]:
    """Return lightweight repository health signals for doctor/self-check."""
    db = db_path()
    ev = events_path()
    quarantine = quarantine_dir()
    out: dict[str, Any] = {
        "db_path": str(db),
        "db_exists": db.exists(),
        "db_readable": False,
        "db_writable": False,
        "journal_mode": "",
        "integrity_check": "",
        "audit_events": 0,
        "events_path": str(ev),
        "events_jsonl_exists": ev.exists(),
        "events_jsonl_bytes": ev.stat().st_size if ev.exists() else 0,
        "events_growth_risk": "green",
        "quarantine_dir": str(quarantine),
        "quarantine_writable": False,
    }
    event_bytes = int(out["events_jsonl_bytes"])
    if event_bytes >= EVENTS_DEGRADED_BYTES:
        out["events_growth_risk"] = "red"
    elif event_bytes >= EVENTS_WARN_BYTES:
        out["events_growth_risk"] = "yellow"
    try:
        init_repository()
        out["db_exists"] = db.exists()
        with _connection() as conn:
            out["journal_mode"] = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
            out["integrity_check"] = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
            out["audit_events"] = int(conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0])
            conn.execute("CREATE TEMP TABLE IF NOT EXISTS absorb_health_probe (x INTEGER)")
            out["db_readable"] = True
            out["db_writable"] = True
    except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
        out["error"] = str(exc)
    try:
        quarantine.mkdir(parents=True, exist_ok=True)
        probe = quarantine / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        out["quarantine_writable"] = True
    except OSError as exc:
        out["quarantine_error"] = str(exc)
    return out


def list_by_status(statuses: tuple[str, ...]) -> list[dict[str, Any]]:
    placeholders = ",".join(["?"] * len(statuses))
    with _connection() as conn:
        rows = conn.execute(f"SELECT * FROM file_records WHERE status IN ({placeholders}) ORDER BY last_seen_at DESC", statuses)
        return [dict(row) for row in rows]


def list_pending_review() -> list[dict[str, Any]]:
    return list_by_status(("PENDING_REVIEW",))


def list_quarantine() -> list[dict[str, Any]]:
    return list_by_status(("QUARANTINED",))


SEARCHABLE_CHUNK_STATUSES = ("LOCAL_INDEXED", "SUBMITTED_TO_MS8", "MS8_ACCEPTED")


def search_chunks(query: str, limit: int = 10, statuses: tuple[str, ...] = SEARCHABLE_CHUNK_STATUSES) -> list[dict[str, Any]]:
    q = f"%{query.strip()}%"
    if not query.strip() or not statuses:
        return []
    placeholders = ",".join(["?"] * len(statuses))
    with _connection() as conn:
        rows = conn.execute(
            f"""
            SELECT c.*, f.canonical_path, f.file_type, f.status AS file_status
            FROM chunks c JOIN file_records f ON c.file_id = f.file_id
            WHERE c.text_preview LIKE ?
              AND c.status IN ({placeholders})
              AND f.status NOT IN ('QUARANTINED', 'DELETED', 'ERROR', 'FILTERED')
            ORDER BY c.chunk_index ASC
            LIMIT ?
            """,
            (q, *statuses, int(limit)),
        )
        return [dict(row) for row in rows]


def list_chunks_by_status(statuses: tuple[str, ...], limit: int = 50) -> list[dict[str, Any]]:
    placeholders = ",".join(["?"] * len(statuses))
    with _connection() as conn:
        rows = conn.execute(
            f"""
            SELECT c.*, f.canonical_path, f.file_type, f.status AS file_status
            FROM chunks c JOIN file_records f ON c.file_id = f.file_id
            WHERE c.status IN ({placeholders})
            ORDER BY f.last_seen_at DESC, c.chunk_index ASC
            LIMIT ?
            """,
            (*statuses, int(limit)),
        )
        return [dict(row) for row in rows]
