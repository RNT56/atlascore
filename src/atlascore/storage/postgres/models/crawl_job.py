"""``crawl_job`` table — the durable Postgres job queue (docs/schemas §2.2).

Status machine + DLQ; workers claim with ``SELECT … FOR UPDATE SKIP LOCKED``. No external
broker. ``last_error`` and ``payload`` are sanitized via ``sanitize_for_storage`` BEFORE write
(CLAUDE.md §14) — never store a raw secret or credential-bearing URL.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from atlascore.core.ids import new_id
from atlascore.schemas.crawl_job import JobStatus, JobType
from atlascore.storage.postgres.base import Base

_JOB_TYPES = ", ".join(f"'{t.value}'" for t in JobType)
_JOB_STATUSES = ", ".join(f"'{s.value}'" for s in JobStatus)


class CrawlJob(Base):
    __tablename__ = "crawl_job"

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=new_id)
    source_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("source.id", ondelete="RESTRICT"), nullable=False
    )
    job_type: Mapped[str] = mapped_column(Text(), nullable=False)
    status: Mapped[str] = mapped_column(Text(), nullable=False, server_default=text("'pending'"))
    dedupe_key: Mapped[str] = mapped_column(Text(), nullable=False, unique=True)
    priority: Mapped[int] = mapped_column(Integer(), nullable=False, server_default=text("0"))
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claimed_by: Mapped[str | None] = mapped_column(Text(), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer(), nullable=False, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer(), nullable=False, server_default=text("5"))
    # last_error / payload are passed through sanitize_for_storage() before assignment.
    last_error: Mapped[str | None] = mapped_column(Text(), nullable=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(f"job_type IN ({_JOB_TYPES})", name="job_type_allowed"),
        CheckConstraint(f"status IN ({_JOB_STATUSES})", name="status_allowed"),
        Index("ix_crawl_job_status_scheduled_at", "status", "scheduled_at"),
        Index("ix_crawl_job_source_id", "source_id"),
    )
