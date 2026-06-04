"""Embedding backends (CLAUDE.md §20, docs/schemas §2.6)."""

from __future__ import annotations

from atlascore.enrichment.embeddings.base import EmbeddingBackend, get_embedding_backend
from atlascore.enrichment.embeddings.hashing import HashingEmbeddingBackend

__all__ = ["EmbeddingBackend", "HashingEmbeddingBackend", "get_embedding_backend"]
