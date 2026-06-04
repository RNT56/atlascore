"""Search over processed stores (Postgres FTS in MVP; Meilisearch only if outgrown)."""

from __future__ import annotations

from atlascore.storage.search.query import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    SearchHit,
    search_documents,
)

__all__ = ["DEFAULT_LIMIT", "MAX_LIMIT", "SearchHit", "search_documents"]
