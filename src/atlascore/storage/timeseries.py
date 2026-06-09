"""``time_series_point`` persistence: idempotent observation upsert + series read (B9).

Writes are **idempotent on the natural key** ``(provider, series_id, ts, vintage)`` via
``ON CONFLICT … DO UPDATE`` against the ``NULLS NOT DISTINCT`` unique constraint (R1) — re-fetching
the same series never duplicates points, and a revised value overwrites in place. Reads back an
ordered (ascending ``ts``) window for ``get_macro_series``; ``value`` stays a ``Decimal`` so the MCP
tool can render it as a string and preserve ``numeric`` precision (no float rounding).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from atlascore.connectors_sdk.types import ParsedSeries
from atlascore.core.ids import new_id
from atlascore.schemas import SCHEMA_VERSION
from atlascore.storage.postgres.models import TimeSeriesPoint

_NATURAL_KEY = "time_series_point_natural_key"
DEFAULT_SERIES_LIMIT = 1000
MAX_SERIES_LIMIT = 2000


def upsert_observations(session: Session, series: ParsedSeries) -> int:
    """Idempotently upsert a parsed series' observations; return the number of points written."""
    if not series.observations:
        return 0
    rows = [
        {
            "id": new_id(),
            "ts": obs.ts,
            "provider": series.provider,
            "series_id": series.series_id,
            "kind": "observation",
            "value": obs.value,
            "unit": series.unit,
            "vintage": obs.vintage,
            "schema_version": SCHEMA_VERSION,
        }
        for obs in series.observations
    ]
    stmt = insert(TimeSeriesPoint).values(rows)
    stmt = stmt.on_conflict_do_update(
        constraint=_NATURAL_KEY,
        set_={
            "value": stmt.excluded.value,
            "unit": stmt.excluded.unit,
            "kind": stmt.excluded.kind,
        },
    )
    session.execute(stmt)
    return len(rows)


@dataclass(frozen=True)
class SeriesPoint:
    ts: datetime
    value: Decimal | None
    vintage: datetime | None


@dataclass(frozen=True)
class MacroSeries:
    provider: str
    series_id: str
    unit: str | None
    points: list[SeriesPoint]
    truncated: bool


def read_macro_series(
    session: Session,
    *,
    provider: str,
    series_id: str,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = DEFAULT_SERIES_LIMIT,
) -> MacroSeries | None:
    """Return a series + ordered observations, or ``None`` if the series has no points.

    ``start`` is inclusive, ``end`` exclusive. ``limit`` is clamped to ``[1, MAX_SERIES_LIMIT]``;
    one extra row is fetched to set ``truncated`` honestly.
    """
    capped = max(1, min(limit, MAX_SERIES_LIMIT))
    stmt = select(TimeSeriesPoint).where(
        TimeSeriesPoint.provider == provider,
        TimeSeriesPoint.series_id == series_id,
        TimeSeriesPoint.kind == "observation",
    )
    if start is not None:
        stmt = stmt.where(TimeSeriesPoint.ts >= start)
    if end is not None:
        stmt = stmt.where(TimeSeriesPoint.ts < end)
    stmt = stmt.order_by(TimeSeriesPoint.ts).limit(capped + 1)

    rows = list(session.scalars(stmt))
    if not rows:
        return None
    truncated = len(rows) > capped
    rows = rows[:capped]
    return MacroSeries(
        provider=provider,
        series_id=series_id,
        unit=next((r.unit for r in rows if r.unit is not None), None),
        points=[SeriesPoint(ts=r.ts, value=r.value, vintage=r.vintage) for r in rows],
        truncated=truncated,
    )


@dataclass(frozen=True)
class SeriesRef:
    """One distinct stored series, for the WebUI macro browser."""

    provider: str
    series_id: str
    unit: str | None
    point_count: int
    latest_ts: datetime | None


def list_series(session: Session, *, limit: int = 500) -> list[SeriesRef]:
    """Return the distinct stored series (provider, series_id) with point count + latest ts."""
    stmt = (
        select(
            TimeSeriesPoint.provider,
            TimeSeriesPoint.series_id,
            func.max(TimeSeriesPoint.unit),
            func.count(),
            func.max(TimeSeriesPoint.ts),
        )
        .where(TimeSeriesPoint.kind == "observation")
        .group_by(TimeSeriesPoint.provider, TimeSeriesPoint.series_id)
        .order_by(TimeSeriesPoint.provider, TimeSeriesPoint.series_id)
        .limit(limit)
    )
    return [
        SeriesRef(provider=p, series_id=s, unit=u, point_count=c, latest_ts=t)
        for p, s, u, c, t in session.execute(stmt).all()
    ]
