"""time_series_point yearly RANGE partitions + crawl_job job_type 'partition' (B17)

Carves the deploy-time current + next calendar year into bounded yearly partitions of the
``time_series_point`` parent (created in 0006), keeping ``time_series_point_default`` as the catch-all.
Because DEFAULT may already hold rows for those years, a bare ATTACH is rejected — so each year uses
the detach-DEFAULT / create-bounded-child / move-matching-rows / re-attach-DEFAULT dance in one
transaction. The parent's composite PK ``(id, ts)`` + ``UNIQUE NULLS NOT DISTINCT`` natural key are
inherited unchanged; all children match the ``time_series_point*`` prefix so they stay excluded from
the autogenerate drift-check (``migrations/env.py:_is_raw_sql_table``).

Also widens the ``crawl_job`` job_type CHECK to accept ``'partition'`` (the new
``JobType.PARTITION`` used by the partition-maintenance job).

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-05 00:00:00+00:00
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PARENT = "time_series_point"
_DEFAULT = "time_series_point_default"
_OLD_JOB_TYPES = "'fetch', 'parse', 'enrich', 'alert', 'freshness'"
_NEW_JOB_TYPES = "'fetch', 'parse', 'enrich', 'alert', 'freshness', 'partition'"
_CK = "ck_crawl_job_job_type_allowed"


def _carve_year(year: int) -> None:
    """Create the bounded child for ``year`` and move its rows out of DEFAULT (with-rows safe)."""
    name = f"{_PARENT}_{year}"
    lo, hi = f"{year}-01-01 00:00:00+00", f"{year + 1}-01-01 00:00:00+00"
    # Idempotent: skip if the child already exists. The introspection needs a live bind, so in
    # offline (`--sql`) mode — which has only a MockConnection — emit the DDL unconditionally
    # (an offline script targets a fresh DB, where the child does not yet exist).
    if not op.get_context().as_sql:
        exists = op.get_bind().exec_driver_sql(f"SELECT to_regclass('{name}')").scalar()
        if exists is not None:
            return
    op.execute(f"ALTER TABLE {_PARENT} DETACH PARTITION {_DEFAULT}")
    op.execute(f"CREATE TABLE {name} PARTITION OF {_PARENT} FOR VALUES FROM ('{lo}') TO ('{hi}')")
    op.execute(
        f"WITH moved AS (DELETE FROM {_DEFAULT} WHERE ts >= '{lo}' AND ts < '{hi}' RETURNING *) "
        f"INSERT INTO {_PARENT} SELECT * FROM moved"
    )
    op.execute(f"ALTER TABLE {_PARENT} ATTACH PARTITION {_DEFAULT} DEFAULT")


def _yearly_children() -> list[str]:
    # Offline (`--sql`) mode has no live bind to introspect existing children; an offline downgrade
    # script can't enumerate them, so emit nothing for the partition-collapse loop.
    if op.get_context().as_sql:
        return []
    rows = op.get_bind().exec_driver_sql(
        "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
        "JOIN pg_class p ON p.oid = i.inhparent "
        f"WHERE p.relname = '{_PARENT}' ORDER BY c.relname"
    )
    return [r[0] for r in rows if re.fullmatch(rf"{_PARENT}_\d{{4}}", r[0])]


def upgrade() -> None:
    year = datetime.now(UTC).year
    _carve_year(year)
    _carve_year(year + 1)
    # Widen the job_type CHECK to accept 'partition' (JobType.PARTITION).
    op.execute(f"ALTER TABLE crawl_job DROP CONSTRAINT {_CK}")
    op.execute(f"ALTER TABLE crawl_job ADD CONSTRAINT {_CK} CHECK (job_type IN ({_NEW_JOB_TYPES}))")


def downgrade() -> None:
    # Restore the original job_type CHECK (drop any 'partition' rows first to satisfy it).
    op.execute("DELETE FROM crawl_job WHERE job_type = 'partition'")
    op.execute(f"ALTER TABLE crawl_job DROP CONSTRAINT {_CK}")
    op.execute(f"ALTER TABLE crawl_job ADD CONSTRAINT {_CK} CHECK (job_type IN ({_OLD_JOB_TYPES}))")
    # Collapse every yearly child back into DEFAULT (detach child → reinsert via parent → drop).
    for child in _yearly_children():
        op.execute(f"ALTER TABLE {_PARENT} DETACH PARTITION {child}")
        op.execute(f"INSERT INTO {_PARENT} SELECT * FROM {child}")
        op.execute(f"DROP TABLE {child}")
