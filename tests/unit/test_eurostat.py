"""Unit/contract tests for the Eurostat connector (offline; respx-mocked HTTP, no live net)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import respx

from atlascore.connectors.eurostat.series import EurostatConnector, _parse_period
from atlascore.connectors_sdk import FetchContext, SafeFetcher
from atlascore.core.clock import FixedClock
from atlascore.core.errors import ConfigError, FetchError

pytestmark = pytest.mark.unit

_FIX = Path(__file__).parent.parent / "fixtures" / "eurostat"
SUCCESS = (_FIX / "hicp_ea_success.json").read_bytes()
EMPTY = (_FIX / "empty.json").read_bytes()
MALFORMED = (_FIX / "malformed.json").read_bytes()

NOW = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)
SERIES_ID = "prc_hicp_manr.CP00.EA.RCH_A"


def _fetcher() -> SafeFetcher:
    return SafeFetcher(
        client=httpx.Client(),
        clock=FixedClock(NOW),
        resolver=lambda _host: ["93.184.216.34"],  # public -> SSRF passes
        sleep=lambda _s: None,
        pin_connections=False,
    )


# --- parse_observations (golden; JSON-stat decode) ---


def test_parse_observations_golden() -> None:
    series = EurostatConnector().parse_observations(SUCCESS, series_id=SERIES_ID)
    assert series.provider == "eurostat"
    assert series.series_id == SERIES_ID
    assert series.unit == "Annual rate of change"  # derived from the JSON-stat unit dimension
    obs = series.observations
    assert len(obs) == 4
    # sorted ascending by ts; values decoded from the flat value map via JSON-stat strides
    assert obs[0].ts == datetime(2025, 9, 1, tzinfo=UTC)
    assert obs[0].value == Decimal("2.2")
    assert obs[-1].ts == datetime(2025, 12, 1, tzinfo=UTC)
    assert obs[-1].value == Decimal("2.0")


def test_parse_empty_series() -> None:
    assert EurostatConnector().parse_observations(EMPTY, series_id="X").observations == []


def test_parse_malformed_raises() -> None:
    with pytest.raises(ValueError, match="not valid JSON"):
        EurostatConnector().parse_observations(MALFORMED, series_id="X")


def test_parse_non_jsonstat_raises() -> None:
    with pytest.raises(ValueError, match="JSON-stat"):
        EurostatConnector().parse_observations(b'{"foo":1}', series_id="X")


def test_parse_period_frequencies() -> None:
    assert _parse_period("2025-09") == datetime(2025, 9, 1, tzinfo=UTC)
    assert _parse_period("2025") == datetime(2025, 1, 1, tzinfo=UTC)
    assert _parse_period("2025-Q3") == datetime(2025, 7, 1, tzinfo=UTC)
    assert _parse_period("2025Q3") == datetime(2025, 7, 1, tzinfo=UTC)
    assert _parse_period("bogus") is None


# --- fetch (contract via respx) ---


@respx.mock
def test_fetch_builds_dataset_url_with_filters() -> None:
    route = respx.get(url__regex=r"https://ec\.europa\.eu/eurostat/api/dissemination/.*").mock(
        return_value=httpx.Response(
            200, content=SUCCESS, headers={"content-type": "application/json"}
        )
    )
    ctx = FetchContext(
        feed_url="https://ec.europa.eu/eurostat/api/dissemination",
        params={"dataset": "prc_hicp_manr", "series_id": SERIES_ID, "coicop": "CP00", "geo": "EA"},
    )
    outcome = EurostatConnector().fetch(_fetcher(), ctx)
    assert outcome.status == 200
    requested = str(route.calls[0].request.url)
    assert "/statistics/1.0/data/prc_hicp_manr" in requested
    assert "format=JSON" in requested
    assert "coicop=CP00" in requested and "geo=EA" in requested
    assert (
        "series_id" not in requested and "dataset=" not in requested
    )  # reserved, not query filters


def test_fetch_missing_dataset_raises() -> None:
    ctx = FetchContext(feed_url="https://ec.europa.eu/eurostat/api/dissemination")
    with pytest.raises(ConfigError, match="dataset"):
        EurostatConnector().fetch(_fetcher(), ctx)


@respx.mock
def test_fetch_rejects_wrong_mime() -> None:
    respx.get(url__regex=r"https://ec\.europa\.eu/.*").mock(
        return_value=httpx.Response(200, content=b"<html/>", headers={"content-type": "text/html"})
    )
    ctx = FetchContext(
        feed_url="https://ec.europa.eu/eurostat/api/dissemination",
        params={"dataset": "prc_hicp_manr", "series_id": SERIES_ID},
    )
    with pytest.raises(FetchError, match="MIME"):
        EurostatConnector().fetch(_fetcher(), ctx)
