"""ECB (European Central Bank) connector — euro-area macro time-series (v1, docs/connectors §8).

Fetches one series from the **free, official, keyless** ECB Data Portal API
(``/service/data/{flowRef}/{seriesKey}?format=csvdata``) and parses the CSV into scalar
observations for ``time_series_point``. Modeled on the FRED connector (same ``timeseries``
output_kind + ``parse_observations`` contract), with two differences:

- **No credential.** The ECB API needs no key, so ``credentials.ref`` is null and nothing secret
  ever rides in the URL.
- **Unit comes from the payload.** ECB's CSV carries a ``UNIT`` column (e.g. ``USD``), so the
  connector fills :attr:`ParsedSeries.unit` from the data instead of relying on an operator label.

The series is addressed by ``fetch.params.flow_ref`` (e.g. ``EXR``) + ``fetch.params.series_key``
(e.g. ``D.USD.EUR.SP00.A``); ``fetch.params.series_id`` is the stable id stored on each point.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from atlascore.connectors_sdk.base import Connector, FetchContext, OutputKind
from atlascore.connectors_sdk.fetcher import SafeFetcher
from atlascore.connectors_sdk.types import FetchOutcome, Observation, ParsedSeries
from atlascore.core.errors import ConfigError

PROVIDER = "ecb"
_DATA_PATH = "/service/data"
# A missing/withheld ECB observation is an empty cell (or NaN); never zero-fill it (§15).
_MISSING = {"", "na", "n/a", "nan", "."}
_QUARTER_RE = re.compile(r"^(\d{4})-Q([1-4])$")
_YEAR_RE = re.compile(r"^\d{4}$")


class EcbConnector(Connector):
    """Fetch + parse one ECB Data Portal series (CSV). Stateless; network only via the fetcher."""

    name = "ecb"
    accepted_mime_types = frozenset({"text/csv", "application/csv"})
    output_kind = OutputKind.TIMESERIES

    def fetch(self, fetcher: SafeFetcher, ctx: FetchContext) -> FetchOutcome:
        flow_ref = ctx.params.get("flow_ref")
        series_key = ctx.params.get("series_key")
        if not flow_ref or not series_key:
            raise ConfigError(
                "ECB source missing fetch.params.flow_ref and/or series_key "
                "(e.g. flow_ref=EXR, series_key=D.USD.EUR.SP00.A)"
            )
        query = urlencode({"format": "csvdata"})
        url = f"{ctx.feed_url.rstrip('/')}{_DATA_PATH}/{flow_ref}/{series_key}?{query}"
        # Like FRED: no conditional headers — idempotent upserts on the natural key make refetch safe.
        return fetcher.fetch(
            url,
            timeout=ctx.timeout,
            max_requests=ctx.max_requests,
            window_seconds=ctx.window_seconds,
            accept_mime=self.accepted_mime_types,
        )

    def parse_observations(self, content: bytes, *, series_id: str) -> ParsedSeries:
        reader = csv.DictReader(io.StringIO(content.decode("utf-8", errors="replace")))
        cols = reader.fieldnames or []
        if "TIME_PERIOD" not in cols or "OBS_VALUE" not in cols:
            raise ValueError("ECB CSV missing TIME_PERIOD/OBS_VALUE columns")

        observations: list[Observation] = []
        unit: str | None = None
        for row in reader:
            ts = _parse_period(row.get("TIME_PERIOD"))
            if ts is None:
                continue
            observations.append(Observation(ts=ts, value=_parse_value(row.get("OBS_VALUE"))))
            if unit is None:
                candidate = (row.get("UNIT") or "").strip()
                unit = candidate or None
        return ParsedSeries(
            provider=PROVIDER, series_id=series_id, unit=unit, observations=observations
        )


def _parse_period(value: object) -> datetime | None:
    """Map an ECB TIME_PERIOD (daily/monthly/quarterly/annual) to a UTC timestamp; gaps → None."""
    if not isinstance(value, str):
        return None
    v = value.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m"):
        try:
            return datetime.strptime(v, fmt).replace(tzinfo=UTC)
        except ValueError:
            pass
    if _YEAR_RE.match(v):
        return datetime(int(v), 1, 1, tzinfo=UTC)
    q = _QUARTER_RE.match(v)
    if q:
        return datetime(int(q.group(1)), (int(q.group(2)) - 1) * 3 + 1, 1, tzinfo=UTC)
    return None


def _parse_value(value: object) -> Decimal | None:
    if not isinstance(value, str) or value.strip().lower() in _MISSING:
        return None
    try:
        return Decimal(value.strip())
    except InvalidOperation:
        return None
