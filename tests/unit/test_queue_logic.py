"""Unit tests for the pure queue helpers (backoff)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from atlascore.storage.queue import retry_delay

pytestmark = pytest.mark.unit


def test_retry_delay_is_exponential_and_capped() -> None:
    d1 = retry_delay(1)
    d2 = retry_delay(2)
    d3 = retry_delay(3)
    assert d1 == timedelta(seconds=30)
    assert d2 == timedelta(seconds=60)
    assert d3 == timedelta(seconds=120)
    # Monotonic non-decreasing and capped at one hour.
    assert retry_delay(99) == timedelta(hours=1)
    assert retry_delay(1) <= retry_delay(2) <= retry_delay(99)


def test_retry_delay_handles_low_attempts() -> None:
    assert retry_delay(0) == retry_delay(1)  # clamps to >= 1
