"""Infrastructure adapters for ledger-v1."""

from .embedding_projection import (
    EMBEDDING_BUILDER_VERSION,
    EMBEDDING_PROJECTION_NAME,
    EMBEDDING_PROJECTION_SCHEMA,
    EmbeddingProjectionEntry,
    EmbeddingProjectionSnapshot,
    embedding_projection_rebuild_reasons,
    read_embedding_projection,
    write_embedding_projection,
)
from .graph_projection import GraphProjectionAdapter
from .jsonl_ledger import JsonlRecordStore
from .search_projection import SearchProjectionAdapter
from .sqlite_projection import (
    ProjectionBuildResult,
    ProjectionFreshness,
    ProjectionManifest,
    SQLiteProjectionBuilder,
)
from .sqlite_projection_adapter import SQLiteProjectionAdapter

__all__ = [
    "EMBEDDING_BUILDER_VERSION",
    "EMBEDDING_PROJECTION_NAME",
    "EMBEDDING_PROJECTION_SCHEMA",
    "EmbeddingProjectionEntry",
    "EmbeddingProjectionSnapshot",
    "GraphProjectionAdapter",
    "JsonlRecordStore",
    "ProjectionBuildResult",
    "ProjectionFreshness",
    "ProjectionManifest",
    "SQLiteProjectionAdapter",
    "SQLiteProjectionBuilder",
    "SearchProjectionAdapter",
    "embedding_projection_rebuild_reasons",
    "read_embedding_projection",
    "write_embedding_projection",
]
