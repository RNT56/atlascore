"""alert (rule-based alerts, B13)

Creates the ``alert`` table (docs/schemas/README.md §2.10). A normal table — no GIN/partition — so
it is created with ``op.create_table`` and is autogenerate/drift-checked (unlike the raw-SQL
enrichment/summary tables). ``dedupe_key`` is ``unique`` (idempotent alert creation); a
``btree(status, severity, created_at)`` index serves ``get_alerts``. ``evidence_doc_ids`` is a
non-referential ``text[]`` (reconciled by purge, R3); ``detail`` is sanitized before write (§14).

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-04 00:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "alert",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("alert_type", sa.Text(), nullable=False),
        sa.Column("rule_id", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'open'"), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("dedupe_key", sa.Text(), nullable=False),
        sa.Column(
            "evidence_doc_ids",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "detail",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivery_status", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "status IN ('open', 'acknowledged', 'resolved', 'purged')",
            name=op.f("ck_alert_status_allowed"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alert")),
        sa.UniqueConstraint("dedupe_key", name=op.f("uq_alert_dedupe_key")),
    )
    op.create_index(
        "ix_alert_status_severity_created_at",
        "alert",
        ["status", "severity", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_alert_status_severity_created_at", table_name="alert")
    op.drop_table("alert")
