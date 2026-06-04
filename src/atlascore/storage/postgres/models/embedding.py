"""``embedding`` table — pgvector chunk embeddings for ANN retrieval (docs/schemas §2.6).

**ONE active embedding model + dim per deployment (R8).** ``pgvector``'s ``vector(N)`` requires a
fixed dimension per ANN index, so ``N`` is pinned in migration ``0007`` (``EMBEDDING_DIM``, default
**384** — matches both the deterministic MVP backend and the future ``all-MiniLM-L6-v2`` swap).
Switching backend/dim is a documented, bounded, resumable backfill + reindex (runbook), never an
online change.

**Idempotent re-embedding (R13).** ``unique(document_id, chunk_index, model_name, model_version)``;
``upsert_embeddings`` skips when the stored ``source_hash`` is unchanged (a no-op, mirroring the
summary cache rule). The HNSW ANN index and the unique constraint are created via **raw SQL** in
0007 (autogenerate cannot round-trip an HNSW index), so the whole ``embedding`` table is excluded
from the drift-check (``_include_object``). The ORM model maps columns for querying/upserts only.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, Integer, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from atlascore.core.ids import new_id
from atlascore.storage.postgres.base import Base

# Fixed embedding dimension for this deployment (R8). Mirrors the ``vector(384)`` column DDL in
# migration 0007 and ``Settings.embedding_dim``. Changing it = the R8 backfill+reindex runbook.
EMBEDDING_DIM = 384


class Embedding(Base):
    __tablename__ = "embedding"

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=new_id)
    document_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("document.id", ondelete="RESTRICT"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer(), nullable=False)
    model_name: Mapped[str] = mapped_column(Text(), nullable=False)
    model_version: Mapped[str] = mapped_column(Text(), nullable=False)
    dim: Mapped[int] = mapped_column(Integer(), nullable=False)
    vector: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    source_hash: Mapped[str] = mapped_column(Text(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
