"""llm_spend cost ledger (B21, §20 hard monthly spend cap)

A normal autogenerate-managed table (like 0009 alert) — one row per paid hosted-LLM call. The
summary pipeline sums month-to-date ``est_cost_eur`` by ``period_ym`` before any paid call and
degrades to cache-only over LLM_MONTHLY_SPEND_CAP_EUR. ``summary_id`` is a plain (non-FK) uuid so
the ledger stays decoupled from the raw-SQL ``summary`` table.

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-05 00:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_spend",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("request_kind", sa.Text(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("est_cost_eur", sa.Numeric(12, 6), nullable=False),
        sa.Column("period_ym", sa.Text(), nullable=False),
        sa.Column("summary_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_spend")),
    )
    op.create_index(op.f("ix_llm_spend_period_ym"), "llm_spend", ["period_ym"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_llm_spend_period_ym"), table_name="llm_spend")
    op.drop_table("llm_spend")
