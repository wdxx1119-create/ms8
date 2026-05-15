"""
Whoosh-based keyword search implementation
"""

import re
import time
from datetime import datetime
from pathlib import Path

from whoosh import index
from whoosh.fields import DATETIME, ID, TEXT, Schema
from whoosh.index import LockError
from whoosh.qparser import MultifieldParser, OrGroup
from whoosh.query import DateRange, Term

from .config import get_config
from .file_store import FileMemoryStore
from .utils import list_daily_log_files


class WhooshSearch:
    """Handle Whoosh-based full-text search."""

    def __init__(self):
        self.config = get_config()
        self.file_store = FileMemoryStore()

        # Determine index directory
        index_dir = self.config["settings"]["memory"]["keyword"]["index_dir"]
        self.index_dir = Path(index_dir)

        self.index_dir.mkdir(parents=True, exist_ok=True)

        # Define schema
        self.schema = Schema(
            content=TEXT(stored=True),
            date=DATETIME(stored=True),
            source=ID(stored=True),
            title=TEXT(stored=True),
        )

        # Initialize or open index
        self._init_index()

    def _init_index(self):
        """Initialize Whoosh index."""
        if index.exists_in(self.index_dir):
            self.ix = index.open_dir(self.index_dir)
        else:
            self.ix = index.create_in(self.index_dir, self.schema)

        # Check if reindexing is needed
        if self.file_store.has_memory_md_changed():
            self.reindex_all()

    def _add_document(self, writer, content: str, date: datetime, source: str, title: str = ""):
        """Add a single document to the index."""
        try:
            writer.add_document(content=content, date=date, source=source, title=title)
        except (OSError, RuntimeError, TypeError, ValueError) as e:
            print(f"Error adding document to index: {e}")

    def index_memory_md(self):
        """Index the MEMORY.md file."""
        if not self.config["memory_md"].exists():
            return

        content = self.file_store.read_memory_md()
        with self._writer() as writer:
            writer.delete_by_term("source", "MEMORY.md")
            self._add_document(
                writer,
                content=content,
                date=datetime.now(),
                source="MEMORY.md",
                title="Long-term Memory",
            )

    def index_daily_logs(self):
        """Index all daily log files."""
        daily_files = list_daily_log_files(self.config["memory_dir"], self.config.get("daily_dir"))
        with self._writer() as writer:
            # Clear existing daily log entries
            for log_file in daily_files:
                writer.delete_by_term("source", f"daily_log:{log_file.name}")

            # Add all daily logs
            for log_file in daily_files:
                try:
                    with open(log_file, encoding="utf-8") as f:
                        content = f.read()

                    # Extract date from filename (YYYY-MM-DD*.md)
                    date_str = log_file.stem.split("-")[0:3]
                    parsed = "-".join(date_str) if len(date_str) == 3 else log_file.stem
                    try:
                        log_date = datetime.fromisoformat(parsed)
                    except ValueError:
                        log_date = datetime.fromtimestamp(log_file.stat().st_mtime)

                    self._add_document(
                        writer,
                        content=content,
                        date=log_date,
                        source=f"daily_log:{log_file.name}",
                        title=f"Daily Log - {log_file.stem}",
                    )
                except (OSError, RuntimeError, TypeError, ValueError) as e:
                    print(f"Error indexing {log_file}: {e}")

    def _writer(self):
        """Get a writer with a small retry window for stale locks."""
        last_error = None
        for _attempt in range(5):
            try:
                return self.ix.writer()
            except LockError as exc:
                last_error = exc
                time.sleep(0.1)
        raise last_error

    def reindex_all(self):
        """Reindex all memory files."""
        self.index_memory_md()
        self.index_daily_logs()
        # Update hash to avoid unnecessary reindexing
        self.file_store._load_memory_md_hash()

    def search(
        self,
        query_str: str,
        top_k: int = 5,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        source_filter: str | None = None,
    ) -> list[dict]:
        """
        Search memory with optional filters.

        Args:
            query_str: Search query string
            top_k: Number of results to return
            date_from: Filter results from this date (inclusive)
            date_to: Filter results to this date (inclusive)
            source_filter: Filter by source (e.g., 'MEMORY.md' or 'daily_log:2026-02-25.md')

        Returns:
            List of search results with content, source, date, and score
        """
        with self.ix.searcher() as searcher:
            parser = MultifieldParser(["content", "title"], self.schema, group=OrGroup.factory(0.9))
            query = parser.parse(query_str)

            # Apply date filters if provided
            if date_from or date_to:
                date_query = DateRange("date", date_from, date_to)
                query = query & date_query

            # Apply source filter if provided
            if source_filter:
                source_query = Term("source", source_filter)
                query = query & source_query

            # Perform search
            results = searcher.search(query, limit=top_k)
            # Fallback for Chinese no-space query and strict multi-word misses.
            if len(results) == 0:
                fallback_query_str = self._build_fallback_query(query_str)
                if fallback_query_str:
                    fallback_query = parser.parse(fallback_query_str)
                    if date_from or date_to:
                        date_query = DateRange("date", date_from, date_to)
                        fallback_query = fallback_query & date_query
                    if source_filter:
                        source_query = Term("source", source_filter)
                        fallback_query = fallback_query & source_query
                    results = searcher.search(fallback_query, limit=top_k)

            # Format results
            formatted_results = []
            for hit in results:
                formatted_results.append(
                    {
                        "content": hit["content"],
                        "source": hit["source"],
                        "date": hit["date"],
                        "score": hit.score,
                        "title": hit.get("title", ""),
                    }
                )

            return formatted_results

    def _build_fallback_query(self, query_str: str) -> str:
        s = query_str.strip()
        if not s:
            return ""
        # English multi-word fallback: OR terms.
        parts = [x for x in re.split(r"\s+", s) if x]
        if len(parts) >= 2:
            return " OR ".join(parts)
        # Chinese fallback: unigram + bigram OR query.
        cjk = re.findall(r"[\u4e00-\u9fff]", s)
        if len(cjk) >= 2:
            grams = set(cjk)
            grams.update("".join(cjk[i : i + 2]) for i in range(len(cjk) - 1))
            return " OR ".join(sorted(grams))
        return ""

    def has_index(self) -> bool:
        """Check if index exists and has documents."""
        with self.ix.searcher() as searcher:
            return searcher.doc_count() > 0
