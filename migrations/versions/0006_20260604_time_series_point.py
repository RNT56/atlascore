"""time_series_point (first partitioned table)

Creates the unified macro/market time-series table (docs/schemas/README.md §2.7) via raw SQL —
Alembic autogenerate cannot emit ``PARTITION BY``. RANGE-partitioned on ``ts`` with a composite
PK ``(id, ts)`` (Postgres requires the PK to include the partition key) and a
``UNIQUE NULLS NOT DISTINCT (provider, series_id, ts, vintage)`` natural key (PG15+). A DEFAULT
partition catches every ``ts`` for the MVP (single-operator scale); yearly partitions are a
documented later refinement (runbooks). All ``time_series_point*`` objects are excluded from the
autogenerate drift-check by ``_include_object`` in ``migrations/env.py``.

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-04 00:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE time_series_point (
            id             uuid NOT NULL,
            ts             timestamptz NOT NULL,
            provider       text NOT NULL,
            series_id      text NOT NULL,
            kind           text NOT NULL,
            value          numeric,
            open           numeric,
            high           numeric,
            low            numeric,
            close          numeric,
            volume         numeric,
            unit           text,
            vintage        timestamptz,
            schema_version integer NOT NULL,
            CONSTRAINT time_series_point_pkey PRIMARY KEY (id, ts),
            CONSTRAINT time_series_point_kind_allowed CHECK (kind IN ('observation', 'bar')),
            CONSTRAINT time_series_point_natural_key
                UNIQUE NULLS NOT DISTINCT (provider, series_id, ts, vintage)
        ) PARTITION BY RANGE (ts)
        """
    )
    # MVP catch-all partition; add yearly partitions later for scale (runbooks).
    op.execute(
        "CREATE TABLE time_series_point_default PARTITION OF time_series_point DEFAULT"
    )
    op.execute(
        "CREATE INDEX time_series_point_provider_series_ts_idx "
        "ON time_series_point (provider, series_id, ts)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS time_series_point")
