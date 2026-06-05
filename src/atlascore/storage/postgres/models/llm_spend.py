"""``llm_spend`` — the LLM cost ledger backing the hard monthly spend cap (B21, §20).

One row per paid hosted-LLM call (summaries in MVP). The summary pipeline sums month-to-date spend
(`period_ym`) before any paid call and degrades to cache-only over `LLM_MONTHLY_SPEND_CAP_EUR`. The
``summary_id`` link is a plain (non-FK) uuid so the ledger stays decoupled from the raw-SQL `summary`
table and purge-simple. Normal autogenerate-managed table (migration 0012).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DateTime, Index, Integer, Numeric, Text, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column

from atlascore.core.ids import new_id
from atlascore.storage.postgres.base import Base


class LlmSpend(Base):
    __tablename__ = "llm_spend"

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=new_id)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    provider: Mapped[str] = mapped_column(Text(), nullable=False)
    model: Mapped[str] = mapped_column(Text(), nullable=False)
    request_kind: Mapped[str] = mapped_column(Text(), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer(), nullable=False, server_default=text("0"))
    output_tokens: Mapped[int] = mapped_column(Integer(), nullable=False, server_default=text("0"))
    est_cost_eur: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    # 'YYYY-MM' bucket for month-to-date sums (indexed).
    period_ym: Mapped[str] = mapped_column(Text(), nullable=False)
    # Non-FK provenance link to the produced summary (decoupled from the raw-SQL summary table).
    summary_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    schema_version: Mapped[int] = mapped_column(Integer(), nullable=False, default=1)

    __table_args__ = (Index("ix_llm_spend_period_ym", "period_ym"),)
