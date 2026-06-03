"""Unit tests for the retention/freshness duration grammar."""

from __future__ import annotations

from datetime import timedelta

import pytest

from atlascore.core.durations import parse_duration

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1h", timedelta(hours=1)),
        ("24h", timedelta(hours=24)),
        ("7d", timedelta(days=7)),
        ("2w", timedelta(weeks=2)),
        ("6m", timedelta(days=180)),
        ("2y", timedelta(days=730)),
    ],
)
def test_parses_valid_durations(text: str, expected: timedelta) -> None:
    assert parse_duration(text) == expected


def test_permanent_when_allowed_returns_none() -> None:
    assert parse_duration("permanent", allow_permanent=True) is None


def test_permanent_rejected_for_freshness() -> None:
    with pytest.raises(ValueError, match="permanent"):
        parse_duration("permanent", allow_permanent=False)


def test_hours_rejected_for_retention() -> None:
    with pytest.raises(ValueError, match="hours"):
        parse_duration("12h", allow_hours=False)


@pytest.mark.parametrize("bad", ["", "2x", "d", "-1d", "0d", "1 d", "1.5d", "abc", "2 y"])
def test_rejects_malformed(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_duration(bad, allow_permanent=True)


def test_strips_surrounding_whitespace() -> None:
    assert parse_duration("  permanent  ", allow_permanent=True) is None
    assert parse_duration(" 7d ") == timedelta(days=7)
