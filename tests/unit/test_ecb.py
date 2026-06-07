"""Unit/contract tests for the ECB connector (offline; respx-mocked HTTP, no live network)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import respx

from atlascore.connectors.ecb.series import EcbConnector, _parse_period
from atlascore.connectors_sdk import FetchContext, SafeFetcher
from atlascore.core.clock import FixedClock
from atlascore.core.errors import ConfigError, FetchError

pytestmark = pytest.mark.unit

_FIX = Path(__file__).parent.parent / "fixtures" / "ecb"
SUCCESS = (_FIX / "eurusd_success.csv").read_bytes()
EMPTY = (_FIX / "empty.csv").read_bytes()
MALFORMED = (_FIX / "malformed.csv").read_bytes()

NOW = datetime(2026, 6, 6, 12, 0, tzinfo=UTC)
SERIES_ID = "EXR.D.USD.EUR.SP00.A"


def _fetcher() -> SafeFetcher:
    return SafeFetcher(
        client=httpx.Client(),
        clock=FixedClock(NOW),
        resolver=lambda _host: ["93.184.216.34"],  # public -> SSRF passes
        sleep=lambda _s: None,
    )


# --- parse_observations (golden) ---


def test_parse_observations_golden() -> None:
    series = EcbConnector().parse_observations(SUCCESS, series_id=SERIES_ID)
    assert series.provider == "ecb"
    assert series.series_id == SERIES_ID
    assert series.unit == "USD"  # ECB carries the unit inline (unlike FRED)
    obs = series.observations
    assert len(obs) == 4
    assert obs[0].ts == datetime(2026, 6, 2, tzinfo=UTC)
    assert obs[0].value == Decimal("1.1649")
    assert obs[1].value == Decimal("1.1614")
    assert obs[2].value is None  # blank OBS_VALUE -> None, never zero-filled
    assert obs[3].ts == datetime(2026, 6, 5, tzinfo=UTC)
    assert obs[3].value == Decimal("1.164")
    assert all(o.vintage is None for o in obs)


def test_parse_empty_series() -> None:
    assert EcbConnector().parse_observations(EMPTY, series_id="X").observations == []


def test_parse_malformed_missing_columns() -> None:
    with pytest.raises(ValueError, match="TIME_PERIOD/OBS_VALUE"):
        EcbConnector().parse_observations(MALFORMED, series_id="X")


def test_parse_period_handles_all_frequencies() -> None:
    assert _parse_period("2026-06-05") == datetime(2026, 6, 5, tzinfo=UTC)
    assert _parse_period("2026-06") == datetime(2026, 6, 1, tzinfo=UTC)
    assert _parse_period("2026") == datetime(2026, 1, 1, tzinfo=UTC)
    assert _parse_period("2026-Q3") == datetime(2026, 7, 1, tzinfo=UTC)
    assert _parse_period("not-a-date") is None


# --- fetch (contract via respx) ---


@respx.mock
def test_fetch_builds_csv_url_no_credential() -> None:
    route = respx.get(url__regex=r"https://data-api\.ecb\.europa\.eu/service/data/.*").mock(
        return_value=httpx.Response(200, content=SUCCESS, headers={"content-type": "text/csv"})
    )
    ctx = FetchContext(
        feed_url="https://data-api.ecb.europa.eu",
        params={"flow_ref": "EXR", "series_key": "D.USD.EUR.SP00.A", "series_id": SERIES_ID},
    )
    outcome = EcbConnector().fetch(_fetcher(), ctx)
    assert outcome.status == 200
    requested = str(route.calls[0].request.url)
    assert "/service/data/EXR/D.USD.EUR.SP00.A" in requested
    assert "format=csvdata" in requested
    assert "api_key" not in requested  # ECB is keyless — nothing secret in the URL


def test_fetch_missing_params_raises() -> None:
    ctx = FetchContext(feed_url="https://data-api.ecb.europa.eu", params={"flow_ref": "EXR"})
    with pytest.raises(ConfigError, match="series_key"):
        EcbConnector().fetch(_fetcher(), ctx)


@respx.mock
def test_fetch_rejects_wrong_mime() -> None:
    respx.get(url__regex=r"https://data-api\.ecb\.europa\.eu/.*").mock(
        return_value=httpx.Response(200, content=b"<html/>", headers={"content-type": "text/html"})
    )
    ctx = FetchContext(
        feed_url="https://data-api.ecb.europa.eu",
        params={"flow_ref": "EXR", "series_key": "D.USD.EUR.SP00.A", "series_id": SERIES_ID},
    )
    with pytest.raises(FetchError, match="MIME"):
        EcbConnector().fetch(_fetcher(), ctx)
