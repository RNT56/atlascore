"""``document`` table — the current-state canonical normalized record (docs/schemas §2.4).

Last-write-wins on re-parse (R2): re-parsing UPDATES the row in place. ``content_hash``
(of the parsed body) detects when a re-parse changed output. ``canonical_url`` is unique
**when present** (a partial unique index) so many null-URL docs can coexist while real
URLs dedupe.

Index note: only the plain btree indexes are declared here. The partial-unique
``canonical_url`` index, the FTS ``tsvector`` GIN, and the ``topics``/``entity_ids`` array
GINs are expression/partial indexes that Alembic autogenerate cannot round-trip, so they
are created via raw SQL in migration 0003 and excluded from the autogenerate drift-check
by ``include_object`` in ``migrations/env.py``.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from atlascore.core.ids import new_id
from atlascore.storage.postgres.base import Base


class Document(Base):
    __tablename__ = "document"

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=new_id)
    source_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("source.id", ondelete="RESTRICT"), nullable=False
    )
    raw_document_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("raw_document.id", ondelete="RESTRICT"), nullable=False
    )
    canonical_url: Mapped[str | None] = mapped_column(Text(), nullable=True)
    title: Mapped[str | None] = mapped_column(Text(), nullable=True)
    body_text: Mapped[str | None] = mapped_column(Text(), nullable=True)
    language: Mapped[str | None] = mapped_column(Text(), nullable=True)
    topics: Mapped[list[str]] = mapped_column(
        ARRAY(Text()), nullable=False, server_default=text("'{}'::text[]")
    )
    entity_ids: Mapped[list[str]] = mapped_column(
        ARRAY(Text()), nullable=False, server_default=text("'{}'::text[]")
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_local_published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    content_hash: Mapped[str] = mapped_column(Text(), nullable=False)
    cluster_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    parser_version: Mapped[str] = mapped_column(Text(), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer(), nullable=False, default=1)

    __table_args__ = (
        Index("ix_document_published_at", "published_at"),
        Index("ix_document_source_id_published_at", "source_id", "published_at"),
        Index("ix_document_cluster_id", "cluster_id"),
    )
