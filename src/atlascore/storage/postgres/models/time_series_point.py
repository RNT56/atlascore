"""``time_series_point`` — unified macro observations + market bars (docs/schemas §2.7).

**The first RANGE-partitioned table.** Partitioned by ``ts`` with a **composite PK ``(id, ts)``**
(Postgres requires a PK/unique on a partitioned table to include all partition-key columns) and a
``UNIQUE NULLS NOT DISTINCT (provider, series_id, ts, vintage)`` natural key for idempotent
upserts (PG15+). ``kind`` discriminates: macro uses ``value``; market bars use the OHLCV columns.

This table is created via **raw SQL** in migration ``0006`` (Alembic autogenerate cannot emit
``PARTITION BY``), so it is **excluded from the autogenerate drift-check** by ``_include_object``
in ``migrations/env.py`` (matched by the ``time_series_point`` name prefix). The ORM model below
maps the columns for querying/upserts only; it declares **no indexes/constraints** beyond the
composite PK SQLAlchemy needs to map the row — the real constraints live in the migration.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DateTime, Integer, Numeric, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from atlascore.core.ids import new_id
from atlascore.storage.postgres.base import Base


class TimeSeriesPoint(Base):
    __tablename__ = "time_series_point"

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=new_id)
    # ts is part of the composite PK because the table is RANGE-partitioned on it (§2.7).
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    provider: Mapped[str] = mapped_column(Text(), nullable=False)
    series_id: Mapped[str] = mapped_column(Text(), nullable=False)
    kind: Mapped[str] = mapped_column(Text(), nullable=False)
    value: Mapped[Decimal | None] = mapped_column(Numeric(), nullable=True)
    open: Mapped[Decimal | None] = mapped_column(Numeric(), nullable=True)
    high: Mapped[Decimal | None] = mapped_column(Numeric(), nullable=True)
    low: Mapped[Decimal | None] = mapped_column(Numeric(), nullable=True)
    close: Mapped[Decimal | None] = mapped_column(Numeric(), nullable=True)
    volume: Mapped[Decimal | None] = mapped_column(Numeric(), nullable=True)
    unit: Mapped[str | None] = mapped_column(Text(), nullable=True)
    vintage: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    schema_version: Mapped[int] = mapped_column(Integer(), nullable=False, default=1)
