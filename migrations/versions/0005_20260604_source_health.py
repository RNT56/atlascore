"""source_health

Creates the per-source freshness/health snapshot table (docs/schemas/README.md §2.12). One row
per source (PK = source_id, FK RESTRICT); `freshness_lag` is an `interval`; `status` is CHECK-
bounded; one plain btree index on `status` (autogenerate-managed). Current snapshot only.

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-04 00:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "source_health",
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_item_published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("freshness_lag", sa.Interval(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'ok'"), nullable=False),
        sa.Column(
            "consecutive_failures", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("success_rate_24h", sa.Numeric(), nullable=True),
        sa.Column("parse_quality", sa.Numeric(), nullable=True),
        sa.Column("duplicate_rate", sa.Numeric(), nullable=True),
        sa.Column("item_count_recent", sa.Integer(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('ok', 'warn', 'stale', 'error', 'cooling_down')",
            name=op.f("ck_source_health_status_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["source.id"],
            name=op.f("fk_source_health_source_id_source"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("source_id", name=op.f("pk_source_health")),
    )
    op.create_index("ix_source_health_status", "source_health", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_source_health_status", table_name="source_health")
    op.drop_table("source_health")
