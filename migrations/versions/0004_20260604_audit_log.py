"""audit_log

Creates the append-only ``audit_log`` table (docs/schemas/README.md §2.11). All indexes are
plain btrees declared on the ORM model (autogenerate-managed); no expression/partial indexes,
so nothing is excluded from the drift-check. The table has **no foreign keys** by design — it
must outlive purge/deletion of the rows it references (``source_id`` / ``target_ref``).

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-04 00:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("tool_name", sa.Text(), nullable=True),
        sa.Column("source_id", sa.Uuid(), nullable=True),
        sa.Column("target_ref", sa.Text(), nullable=True),
        sa.Column(
            "retention_class",
            sa.Text(),
            server_default=sa.text("'routine'"),
            nullable=False,
        ),
        sa.Column(
            "detail",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "actor IN ('system', 'cli', 'mcp')",
            name=op.f("ck_audit_log_actor_allowed"),
        ),
        sa.CheckConstraint(
            "retention_class IN ('routine', 'purge')",
            name=op.f("ck_audit_log_retention_class_allowed"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    op.create_index("ix_audit_log_action", "audit_log", ["action"], unique=False)
    op.create_index("ix_audit_log_ts", "audit_log", ["ts"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_audit_log_ts", table_name="audit_log")
    op.drop_index("ix_audit_log_action", table_name="audit_log")
    op.drop_table("audit_log")
