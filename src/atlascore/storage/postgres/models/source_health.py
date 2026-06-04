"""``source_health`` table — the per-source freshness/health snapshot (docs/schemas §2.12).

Current snapshot only (one row per source; no history table in MVP). Computed by the freshness
job (`worker/freshness`) from `crawl_job` + `document`; read by `atlas health` / `atlas digest`
and the read-only `get_source_health` MCP tool. `freshness_lag` is a Postgres `interval`
(`now - coalesce(last_item_published_at, last_success_at)`); `status` is compared against the
source's `freshness` thresholds. FK is `ON DELETE RESTRICT` (R17).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Interval,
    Numeric,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from atlascore.schemas.source_health import HealthStatus
from atlascore.storage.postgres.base import Base

_STATUSES = ", ".join(f"'{s.value}'" for s in HealthStatus)


class SourceHealth(Base):
    __tablename__ = "source_health"

    source_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("source.id", ondelete="RESTRICT"), primary_key=True
    )
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_item_published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    freshness_lag: Mapped[timedelta | None] = mapped_column(Interval(), nullable=True)
    status: Mapped[str] = mapped_column(Text(), nullable=False, server_default=text("'ok'"))
    consecutive_failures: Mapped[int] = mapped_column(
        Integer(), nullable=False, server_default=text("0")
    )
    success_rate_24h: Mapped[float | None] = mapped_column(Numeric(asdecimal=False), nullable=True)
    parse_quality: Mapped[float | None] = mapped_column(Numeric(asdecimal=False), nullable=True)
    duplicate_rate: Mapped[float | None] = mapped_column(Numeric(asdecimal=False), nullable=True)
    item_count_recent: Mapped[int | None] = mapped_column(Integer(), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(f"status IN ({_STATUSES})", name="status_allowed"),
        Index("ix_source_health_status", "status"),
    )
