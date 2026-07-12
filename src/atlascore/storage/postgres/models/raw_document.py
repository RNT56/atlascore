"""``raw_document`` table — content-addressed pointer to the gzipped raw payload on disk.

The on-disk blob is the immutable evidence of record; this row is its index. Mutable only
via the audited purge path (docs/schemas §2.3). ``content_hash`` is the content address
(unique), so re-fetching identical bytes is a no-op.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from atlascore.core.ids import new_id
from atlascore.storage.postgres.base import Base


class RawDocument(Base):
    __tablename__ = "raw_document"

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=new_id)
    source_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("source.id", ondelete="RESTRICT"), nullable=False
    )
    crawl_job_id: Mapped[UUID | None] = mapped_column(
        Uuid(), ForeignKey("crawl_job.id", ondelete="RESTRICT"), nullable=True
    )
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    content_hash: Mapped[str] = mapped_column(Text(), nullable=False, unique=True)
    mime_type: Mapped[str | None] = mapped_column(Text(), nullable=True)
    path: Mapped[str] = mapped_column(Text(), nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger(), nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer(), nullable=True)
    etag: Mapped[str | None] = mapped_column(Text(), nullable=True)
    last_modified: Mapped[str | None] = mapped_column(Text(), nullable=True)
    # Final public fetch URL and the local parser for secondary entry payloads (for example an SEC
    # primary filing). Both are provenance/configuration, never a credential-bearing URL.
    origin_url: Mapped[str | None] = mapped_column(Text(), nullable=True)
    parser_ref: Mapped[str | None] = mapped_column(Text(), nullable=True)
    schema_version: Mapped[int] = mapped_column(Integer(), nullable=False, default=1)

    __table_args__ = (Index("ix_raw_document_source_id_fetched_at", "source_id", "fetched_at"),)
