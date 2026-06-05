"""LLM spend ledger helpers (B21, §20): cost estimation, month-to-date sum, and recording.

The price table is a small, conservative, operator-tunable per-1k-token map in EUR — it errs high so
the hard cap trips early rather than late. Unknown models fall back to a default rate. All amounts
are :class:`~decimal.Decimal` (money rule, §18).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from atlascore.storage.postgres.models import LlmSpend

# Conservative EUR per 1,000 tokens (input, output). Operator-tunable; documented in docs/runbooks.
_PRICE_TABLE_EUR: dict[str, tuple[Decimal, Decimal]] = {
    "openai": (Decimal("0.0005"), Decimal("0.0015")),
    "anthropic": (Decimal("0.0008"), Decimal("0.0040")),
    "google": (Decimal("0.0004"), Decimal("0.0012")),
}
_DEFAULT_PRICE_EUR = (Decimal("0.001"), Decimal("0.003"))


def period_ym(now: datetime) -> str:
    """The 'YYYY-MM' month bucket for ``now`` (UTC)."""
    return now.strftime("%Y-%m")


def estimate_cost_eur(provider: str, input_tokens: int, output_tokens: int) -> Decimal:
    """Estimate a call's cost in EUR from the per-provider price table (conservative)."""
    in_rate, out_rate = _PRICE_TABLE_EUR.get(provider, _DEFAULT_PRICE_EUR)
    cost = (Decimal(input_tokens) / 1000 * in_rate) + (Decimal(output_tokens) / 1000 * out_rate)
    return cost.quantize(Decimal("0.000001"))


def month_to_date_spend(session: Session, period: str) -> Decimal:
    """Sum ``est_cost_eur`` for the given 'YYYY-MM' period (0 when none)."""
    total = session.scalar(
        select(func.coalesce(func.sum(LlmSpend.est_cost_eur), 0)).where(
            LlmSpend.period_ym == period
        )
    )
    return Decimal(total or 0)


def record_spend(
    session: Session,
    *,
    provider: str,
    model: str,
    request_kind: str,
    input_tokens: int,
    output_tokens: int,
    now: datetime,
    summary_id: UUID | None = None,
) -> Decimal:
    """Insert one ledger row and return the estimated cost. Caller manages the transaction."""
    cost = estimate_cost_eur(provider, input_tokens, output_tokens)
    session.add(
        LlmSpend(
            ts=now,
            provider=provider,
            model=model,
            request_kind=request_kind,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            est_cost_eur=cost,
            period_ym=period_ym(now),
            summary_id=summary_id,
            schema_version=1,
        )
    )
    return cost
