"""source.checkpoint jsonb (runtime-owned incremental crawl cursor, B20)

Adds a nullable ``checkpoint`` jsonb to ``source`` — the runtime-owned incremental-fetch cursor
(NVD lastModified window + paging; SEC newest filingDate). It is distinct from ``config`` (the
lossless YAML round-trip home, operator-authored and overwritten on every ``atlas source sync``):
``checkpoint`` is written only by the worker after a successful fetch and never by the YAML loader,
so re-syncing a source can't clobber its crawl progress. NULL = no cursor yet (full fetch).

A normal additive column (autogenerate/drift-checked); no backfill, reversible.

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-05 00:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("source", sa.Column("checkpoint", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("source", "checkpoint")
