"""Unit tests for the pure partition helpers + CLI registration (B17; offline, no DB)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from atlascore.mcp_server import build_mcp_server  # sanity: MCP surface unchanged by B17
from atlascore.storage.partitions import (
    needed_years,
    partition_name,
    year_bounds,
)

pytestmark = pytest.mark.unit


def test_partition_name() -> None:
    assert partition_name(2026) == "time_series_point_2026"


def test_year_bounds_half_open_utc() -> None:
    lo, hi = year_bounds(2026)
    assert lo == "2026-01-01 00:00:00+00"
    assert hi == "2027-01-01 00:00:00+00"


def test_needed_years() -> None:
    now = datetime(2026, 6, 1, tzinfo=UTC)
    assert needed_years(now, years_ahead=0) == [2026]
    assert needed_years(now, years_ahead=1) == [2026, 2027]
    assert needed_years(now, years_ahead=3) == [2026, 2027, 2028, 2029]


def test_year_guard_rejects_unsafe_identifiers() -> None:
    for bad in (1999, 2101, 0, 99999):
        with pytest.raises(ValueError, match="out of safe range"):
            partition_name(bad)


def test_mcp_surface_unchanged() -> None:
    # B17 is control-plane; the 7-tool read surface must be untouched.
    tools = asyncio.run(build_mcp_server().list_tools())
    assert len(tools) == 7
