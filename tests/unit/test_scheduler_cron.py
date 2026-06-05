"""Unit tests for the scheduler cron-due math + tick bucketing (B19; offline)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from atlascore.core.errors import ConfigError
from atlascore.scheduler.cron import is_due, next_due_after, tick_bucket

pytestmark = pytest.mark.unit


def test_next_due_after() -> None:
    after = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
    nxt = next_due_after("*/15 * * * *", after)
    assert nxt == datetime(2026, 6, 1, 9, 15, tzinfo=UTC)
    daily = next_due_after("0 9 * * *", after)
    assert daily == datetime(2026, 6, 2, 9, 0, tzinfo=UTC)


def test_is_due_first_run_and_window() -> None:
    now = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    assert is_due("*/15 * * * *", last_scheduled=None, now=now) is True  # never run → due
    # last scheduled at 09:00 → next is 09:15 ≤ now(09:30) → due
    assert is_due("*/15 * * * *", last_scheduled=datetime(2026, 6, 1, 9, 0, tzinfo=UTC), now=now)
    # last scheduled at 09:30 → next is 09:45 > now → not due
    assert not is_due("*/15 * * * *", last_scheduled=now, now=now)


def test_malformed_cron_raises() -> None:
    with pytest.raises(ConfigError, match="invalid cron"):
        next_due_after("not a cron", datetime(2026, 6, 1, tzinfo=UTC))


def test_tick_bucket_floors_deterministically() -> None:
    now = datetime(2026, 6, 1, 9, 37, 42, tzinfo=UTC)
    b = tick_bucket(now, timedelta(seconds=60))
    assert b == datetime(2026, 6, 1, 9, 37, 0, tzinfo=UTC)
    # Two times in the same minute share a bucket (stable dedupe key).
    later = datetime(2026, 6, 1, 9, 37, 59, tzinfo=UTC)
    assert tick_bucket(later, timedelta(seconds=60)) == b
