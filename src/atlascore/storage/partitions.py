"""``time_series_point`` yearly RANGE partition management (B17, docs/schemas §2.7).

Migration 0006 created the RANGE-by-``ts`` parent with a single DEFAULT catch-all partition. This
module carves **yearly** child partitions ahead of time so the time-series stays prunable/queryable
at volume, while keeping DEFAULT as the catch-all for any uncarved year. The parent's composite PK
``(id, ts)`` and ``UNIQUE NULLS NOT DISTINCT (provider, series_id, ts, vintage)`` are inherited by
children unchanged. Every object is named ``time_series_point*`` so it stays excluded from the
autogenerate drift-check (``migrations/env.py:_is_raw_sql_table``).

The tricky bit is attaching a bounded child when DEFAULT already holds rows for that range: a bare
``ATTACH`` is rejected (overlap). :func:`ensure_yearly_partitions` uses the safe
detach-DEFAULT / create-bounded-child / move-matching-rows / re-attach-DEFAULT dance in one
transaction. This is the one routine that *moves* time-series rows (raw_document blobs are untouched).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

# Identifier-safety guard: the year is interpolated into a table name, so bound it to a sane range.
_MIN_YEAR = 2000
_MAX_YEAR = 2100
PARENT = "time_series_point"
DEFAULT_PARTITION = "time_series_point_default"


def partition_name(year: int) -> str:
    """The child partition table name for a calendar year (e.g. ``time_series_point_2026``)."""
    _check_year(year)
    return f"{PARENT}_{year}"


def year_bounds(year: int) -> tuple[str, str]:
    """Half-open UTC bounds ``[year-01-01, (year+1)-01-01)`` as Postgres timestamptz literals."""
    _check_year(year)
    return (f"{year}-01-01 00:00:00+00", f"{year + 1}-01-01 00:00:00+00")


def _check_year(year: int) -> None:
    if not (_MIN_YEAR <= year <= _MAX_YEAR):
        raise ValueError(f"year {year} out of safe range [{_MIN_YEAR}, {_MAX_YEAR}]")


def needed_years(now: datetime, *, years_ahead: int = 1) -> list[int]:
    """The set of years that should have a bounded partition: current .. current+years_ahead."""
    return [now.year + offset for offset in range(years_ahead + 1)]


def list_existing_partitions(session: Session) -> list[str]:
    """Child partition table names of the parent (excludes the parent itself), sorted."""
    rows = session.execute(
        text(
            "SELECT c.relname FROM pg_inherits i "
            "JOIN pg_class c ON c.oid = i.inhrelid "
            "JOIN pg_class p ON p.oid = i.inhparent "
            "WHERE p.relname = :parent ORDER BY c.relname"
        ),
        {"parent": PARENT},
    )
    return [r[0] for r in rows]


def partition_for_year_exists(session: Session, year: int) -> bool:
    return (
        session.scalar(text("SELECT to_regclass(:name)"), {"name": partition_name(year)})
        is not None
    )


def partition_row_counts(session: Session) -> dict[str, int]:
    """Row count per child partition (via ``tableoid``), for ``atlas tsp partitions status``."""
    counts: dict[str, int] = {}
    for name in list_existing_partitions(session):
        counts[name] = session.scalar(text(f"SELECT count(*) FROM {name}")) or 0  # noqa: S608 - name is an int-guarded partition identifier
    return counts


def _migrate_year_out_of_default(session: Session, year: int) -> None:
    """Create the bounded child for ``year`` and move its rows out of DEFAULT (the with-rows path).

    DEFAULT may already hold rows in the range, so a bare ATTACH is rejected — detach DEFAULT, create
    the bounded child, move matching rows through the parent into the child, re-attach DEFAULT. One
    transaction (the caller's); brief ACCESS EXCLUSIVE lock, fine at single-operator scale.
    """
    lo, hi = year_bounds(year)
    name = partition_name(year)
    session.execute(text(f"ALTER TABLE {PARENT} DETACH PARTITION {DEFAULT_PARTITION}"))
    session.execute(
        text(f"CREATE TABLE {name} PARTITION OF {PARENT} FOR VALUES FROM ('{lo}') TO ('{hi}')")
    )
    # Move this year's rows out of DEFAULT, routed via the parent into the new bounded child.
    # Identifiers are module constants; lo/hi are int-guarded literal bounds (no user input).
    move_sql = f"WITH moved AS (DELETE FROM {DEFAULT_PARTITION} WHERE ts >= '{lo}' AND ts < '{hi}' RETURNING *) INSERT INTO {PARENT} SELECT * FROM moved"  # noqa: S608
    session.execute(text(move_sql))
    session.execute(text(f"ALTER TABLE {PARENT} ATTACH PARTITION {DEFAULT_PARTITION} DEFAULT"))


@dataclass(frozen=True)
class PartitionMaintenanceResult:
    created: list[str]


def ensure_yearly_partitions(
    session: Session, *, now: datetime, years_ahead: int = 1
) -> PartitionMaintenanceResult:
    """Idempotently ensure current..current+years_ahead yearly partitions exist. Caller's txn."""
    created: list[str] = []
    for year in needed_years(now, years_ahead=years_ahead):
        if not partition_for_year_exists(session, year):
            _migrate_year_out_of_default(session, year)
            created.append(partition_name(year))
    return PartitionMaintenanceResult(created=created)
