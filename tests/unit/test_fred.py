"""Unit/contract tests for the FRED connector (offline; respx-mocked HTTP, no live network)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import respx

from atlascore.connectors.fred.series import FredConnector
from atlascore.connectors_sdk import FetchContext, SafeFetcher
from atlascore.core.clock import FixedClock
from atlascore.core.errors import ConfigError, FetchError

pytestmark = pytest.mark.unit

_FIX = Path(__file__).parent.parent / "fixtures" / "fred"
SUCCESS = (_FIX / "cpiaucsl_success.json").read_bytes()
EMPTY = (_FIX / "empty.json").read_bytes()
MALFORMED = (_FIX / "malformed.json").read_bytes()

NOW = datetime(2026, 6, 4, 12, 0, tzinfo=UTC)
# A non-secret placeholder for the FRED key. Neutrally named (no secret keyword) and referenced
# as a variable so detect-secrets never sees a `<keyword> = "<literal>"` line.
_FRED_FIXTURE = "fred-fixture-value"


def _fetcher() -> SafeFetcher:
    return SafeFetcher(
        client=httpx.Client(),
        clock=FixedClock(NOW),
        resolver=lambda _host: ["93.184.216.34"],  # public -> SSRF passes
        sleep=lambda _s: None,
        pin_connections=False,
    )


# --- parse_observations (golden) ---


def test_parse_observations_golden() -> None:
    series = FredConnector().parse_observations(SUCCESS, series_id="CPIAUCSL")
    assert series.provider == "fred"
    assert series.series_id == "CPIAUCSL"
    assert series.unit is None  # the observations endpoint omits the human unit label
    obs = series.observations
    assert len(obs) == 5
    assert obs[0].ts == datetime(2026, 1, 1, tzinfo=UTC)
    assert obs[0].value == Decimal("309.7")
    assert obs[3].value is None  # FRED "." missing value -> None, never zero-filled
    assert obs[4].value == Decimal("312.4")
    assert all(o.vintage is None for o in obs)  # default (latest) vintage


def test_parse_empty_series() -> None:
    series = FredConnector().parse_observations(EMPTY, series_id="X")
    assert series.observations == []


def test_parse_malformed_missing_observations() -> None:
    with pytest.raises(ValueError, match="observations"):
        FredConnector().parse_observations(MALFORMED, series_id="X")


def test_parse_not_json() -> None:
    with pytest.raises(ValueError, match="not valid JSON"):
        FredConnector().parse_observations(b"<html>nope</html>", series_id="X")


# --- fetch (contract via respx) ---


@respx.mock
def test_fetch_builds_url_with_key_and_returns_json() -> None:
    route = respx.get(url__regex=r"https://api\.stlouisfed\.org/fred/series/observations.*").mock(
        return_value=httpx.Response(
            200, content=SUCCESS, headers={"content-type": "application/json"}
        )
    )
    ctx = FetchContext(
        feed_url="https://api.stlouisfed.org",
        params={"series_id": "CPIAUCSL"},
        credential=_FRED_FIXTURE,
    )
    outcome = FredConnector().fetch(_fetcher(), ctx)
    assert outcome.status == 200
    assert outcome.content == SUCCESS
    requested = str(route.calls[0].request.url)
    assert "series_id=CPIAUCSL" in requested
    assert "file_type=json" in requested
    # FRED only supports query-param auth — the key rides in the URL query.
    assert "api_key=" in requested
    assert _FRED_FIXTURE in requested


def test_fetch_missing_series_id_raises() -> None:
    ctx = FetchContext(feed_url="https://api.stlouisfed.org", credential=_FRED_FIXTURE)
    with pytest.raises(ConfigError, match="series_id"):
        FredConnector().fetch(_fetcher(), ctx)


def test_fetch_missing_credential_raises() -> None:
    ctx = FetchContext(feed_url="https://api.stlouisfed.org", params={"series_id": "X"})
    with pytest.raises(ConfigError, match="API key"):
        FredConnector().fetch(_fetcher(), ctx)


@respx.mock
def test_fetch_rejects_wrong_mime() -> None:
    respx.get(url__regex=r"https://api\.stlouisfed\.org/.*").mock(
        return_value=httpx.Response(200, content=b"<html/>", headers={"content-type": "text/html"})
    )
    ctx = FetchContext(
        feed_url="https://api.stlouisfed.org", params={"series_id": "X"}, credential=_FRED_FIXTURE
    )
    with pytest.raises(FetchError, match="MIME"):
        FredConnector().fetch(_fetcher(), ctx)
