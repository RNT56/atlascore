"""``alert`` table — rule-based alerts with dedupe + evidence doc ids (docs/schemas §2.10).

Powers `get_alerts`. A deterministic ``dedupe_key`` (``unique``) makes alert creation idempotent —
re-evaluating the same fact never double-fires. ``evidence_doc_ids`` is a non-referential ``text[]``
of internal ``document.id`` values (reconciled by the purge job, R3). ``detail`` is sanitized via
``sanitize_for_storage`` before write (§14). Lifecycle ``open → acknowledged → resolved``;
``purged`` when all evidence is erased.

A normal table (no GIN/partition), so it is **autogenerate-managed** (migration ``0009`` uses
``op.create_table`` and it is drift-checked) — unlike the raw-SQL enrichment/summary tables.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, Text, Uuid, func, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from atlascore.core.ids import new_id
from atlascore.schemas.alert import AlertStatus
from atlascore.storage.postgres.base import Base

_STATUSES = ", ".join(f"'{s.value}'" for s in AlertStatus)


class Alert(Base):
    __tablename__ = "alert"

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=new_id)
    alert_type: Mapped[str] = mapped_column(Text(), nullable=False)
    rule_id: Mapped[str] = mapped_column(Text(), nullable=False)
    severity: Mapped[str] = mapped_column(Text(), nullable=False)
    status: Mapped[str] = mapped_column(Text(), nullable=False, server_default=text("'open'"))
    title: Mapped[str] = mapped_column(Text(), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(Text(), nullable=False, unique=True)
    evidence_doc_ids: Mapped[list[str]] = mapped_column(
        ARRAY(Text()), nullable=False, server_default=text("'{}'::text[]")
    )
    detail: Mapped[dict[str, Any]] = mapped_column(
        JSONB(), nullable=False, server_default=text("'{}'::jsonb")
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivery_status: Mapped[str | None] = mapped_column(Text(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    schema_version: Mapped[int] = mapped_column(Integer(), nullable=False, default=1)

    __table_args__ = (
        CheckConstraint(f"status IN ({_STATUSES})", name="status_allowed"),
        Index("ix_alert_status_severity_created_at", "status", "severity", "created_at"),
    )
