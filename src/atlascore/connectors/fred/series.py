"""FRED (Federal Reserve Economic Data) connector — macro time-series (docs/connectors §7).

Fetches one series' observations from the **free, official** FRED API
(``/fred/series/observations``, JSON) and parses them into scalar observations for
``time_series_point``. The series id comes from ``source.config.params.series_id`` and the API
key from the env named by ``credential_ref`` (FRED only supports ``?api_key=…`` query auth).

**Secret handling (§14):** the api-key query parameter is in the request URL, so the URL and any
error mentioning it MUST be sanitized before it reaches a log, error column, or backup
(``sanitize_for_storage`` strips URL query/userinfo — the macro worker routes errors through it).
The stored raw payload (the observations JSON) contains no key.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlencode

from atlascore.connectors_sdk.base import Connector, FetchContext
from atlascore.connectors_sdk.fetcher import SafeFetcher
from atlascore.connectors_sdk.types import FetchOutcome, Observation, ParsedSeries
from atlascore.core.errors import ConfigError

PROVIDER = "fred"
_OBSERVATIONS_PATH = "/fred/series/observations"
# FRED encodes a missing value as ".".
_MISSING = {".", "", "na", "n/a"}


class FredConnector(Connector):
    """Fetch + parse one FRED economic series. Stateless; network only via the fetcher."""

    name = "fred"
    accepted_mime_types = frozenset({"application/json", "text/json"})

    def fetch(self, fetcher: SafeFetcher, ctx: FetchContext) -> FetchOutcome:
        series_id = ctx.params.get("series_id")
        if not series_id:
            raise ConfigError("FRED source missing fetch.params.series_id")
        if not ctx.credential:
            raise ConfigError(
                "FRED source missing API key (set credentials.ref to an env key, e.g. FRED_API_KEY)"
            )
        query = urlencode({"series_id": series_id, "file_type": "json", "api_key": ctx.credential})
        url = f"{ctx.feed_url.rstrip('/')}{_OBSERVATIONS_PATH}?{query}"
        # No ETag/Last-Modified: FRED observations are not conditional-request friendly; idempotent
        # upserts on the natural key make refetching cheap and safe.
        return fetcher.fetch(
            url,
            timeout=ctx.timeout,
            max_requests=ctx.max_requests,
            window_seconds=ctx.window_seconds,
            accept_mime=self.accepted_mime_types,
        )

    def parse_observations(self, content: bytes, *, series_id: str) -> ParsedSeries:
        try:
            data: Any = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError(f"FRED response is not valid JSON: {exc}") from exc
        if not isinstance(data, dict) or "observations" not in data:
            raise ValueError("FRED response missing 'observations'")
        raw_obs = data.get("observations")
        if not isinstance(raw_obs, list):
            raise ValueError("FRED 'observations' is not a list")

        observations: list[Observation] = []
        for item in raw_obs:
            if not isinstance(item, dict):
                continue
            ts = _parse_date(item.get("date"))
            if ts is None:
                continue
            observations.append(Observation(ts=ts, value=_parse_value(item.get("value"))))
        return ParsedSeries(
            provider=PROVIDER,
            series_id=series_id,
            unit=None,  # the human unit label needs /fred/series; the operator may set params.unit
            observations=observations,
        )


def _parse_date(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        return None


def _parse_value(value: object) -> Decimal | None:
    """A missing value (FRED ``"."``) becomes ``None`` — never zero-filled (§15)."""
    if not isinstance(value, str) or value.strip().lower() in _MISSING:
        return None
    try:
        return Decimal(value.strip())
    except InvalidOperation:
        return None
