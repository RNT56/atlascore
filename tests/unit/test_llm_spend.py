"""Unit tests for the LLM spend cost math (B21; pure, offline)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from atlascore.storage.llm_spend import estimate_cost_eur, period_ym

pytestmark = pytest.mark.unit


def test_period_ym() -> None:
    assert period_ym(datetime(2026, 6, 5, 12, 0, tzinfo=UTC)) == "2026-06"
    assert period_ym(datetime(2026, 12, 31, 23, 59, tzinfo=UTC)) == "2026-12"


def test_estimate_cost_known_provider() -> None:
    # openai: 0.0005/1k in, 0.0015/1k out → 1000 in + 1000 out = 0.0005 + 0.0015 = 0.002
    assert estimate_cost_eur("openai", 1000, 1000) == Decimal("0.002000")
    assert estimate_cost_eur("openai", 0, 0) == Decimal("0.000000")


def test_estimate_cost_unknown_provider_uses_default() -> None:
    # default: 0.001/1k in, 0.003/1k out → 1000+1000 = 0.004
    assert estimate_cost_eur("mystery", 1000, 1000) == Decimal("0.004000")


def test_estimate_cost_is_decimal() -> None:
    assert isinstance(estimate_cost_eur("anthropic", 500, 500), Decimal)
