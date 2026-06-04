"""Unit tests for the per-host rate limiter (even-spacing, injected clock)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from atlascore.connectors_sdk.rate_limit import HostRateLimiter

pytestmark = pytest.mark.unit

T0 = datetime(2026, 6, 3, 12, 0, 0, tzinfo=UTC)


def test_spaces_requests_within_a_window() -> None:
    limiter = HostRateLimiter()
    # 2 requests / 1s -> one every 0.5s.
    assert limiter.acquire("h", now=T0, max_requests=2, window_seconds=1) == 0.0
    assert limiter.acquire("h", now=T0, max_requests=2, window_seconds=1) == pytest.approx(0.5)
    assert limiter.acquire("h", now=T0, max_requests=2, window_seconds=1) == pytest.approx(1.0)


def test_no_wait_after_enough_time_passes() -> None:
    limiter = HostRateLimiter()
    limiter.acquire("h", now=T0, max_requests=1, window_seconds=5)
    later = T0 + timedelta(seconds=5)
    assert limiter.acquire("h", now=later, max_requests=1, window_seconds=5) == 0.0


def test_hosts_are_independent() -> None:
    limiter = HostRateLimiter()
    assert limiter.acquire("a", now=T0, max_requests=1, window_seconds=10) == 0.0
    assert limiter.acquire("b", now=T0, max_requests=1, window_seconds=10) == 0.0


def test_rejects_bad_limits() -> None:
    limiter = HostRateLimiter()
    with pytest.raises(ValueError, match="positive"):
        limiter.acquire("h", now=T0, max_requests=0, window_seconds=1)
