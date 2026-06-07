"""Eurostat connector — EU macro time-series via JSON-stat (v1, docs/connectors §8).

Fetches one series from the **free, official, keyless** Eurostat dissemination API
(``/statistics/1.0/data/{dataset}?...&format=JSON``) and parses the **JSON-stat 2.0** response into
scalar observations for ``time_series_point``. Same ``timeseries`` output_kind + ``parse_observations``
contract as FRED/ECB, but the payload is multidimensional: the operator pins every non-time dimension
via ``fetch.params`` (Eurostat dimension codes like ``coicop``/``geo``/``unit``) so the result is a
single series over ``time``; the connector maps each flat ``value`` index back to its time period via
the JSON-stat ``size``/``dimension`` metadata. Like ECB, the human unit label comes from the payload
(the ``unit`` dimension's category label) — not an operator-supplied label.

``fetch.params``: ``dataset`` (required → path) + ``series_id`` (required → stored id) + any Eurostat
dimension filters / ``lastTimePeriod`` / ``sinceTimePeriod`` (all passed through as query params).
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlencode

from atlascore.connectors_sdk.base import Connector, FetchContext, OutputKind
from atlascore.connectors_sdk.fetcher import SafeFetcher
from atlascore.connectors_sdk.types import FetchOutcome, Observation, ParsedSeries
from atlascore.core.errors import ConfigError

PROVIDER = "eurostat"
_DATA_PATH = "/statistics/1.0/data"
# Params the connector consumes itself (everything else is a Eurostat query filter).
_RESERVED = {"dataset", "series_id"}
_QUARTER_RE = re.compile(r"^(\d{4})-?Q([1-4])$")
_YEAR_RE = re.compile(r"^\d{4}$")


class EurostatConnector(Connector):
    """Fetch + parse one Eurostat JSON-stat series. Stateless; network only via the fetcher."""

    name = "eurostat"
    accepted_mime_types = frozenset({"application/json", "text/json"})
    output_kind = OutputKind.TIMESERIES

    def fetch(self, fetcher: SafeFetcher, ctx: FetchContext) -> FetchOutcome:
        dataset = ctx.params.get("dataset")
        if not dataset:
            raise ConfigError("Eurostat source missing fetch.params.dataset (e.g. prc_hicp_manr)")
        query = {"format": "JSON"}
        for key, value in ctx.params.items():
            if key not in _RESERVED and value:
                query[key] = value  # Eurostat dimension filter / lastTimePeriod / sinceTimePeriod
        url = f"{ctx.feed_url.rstrip('/')}{_DATA_PATH}/{dataset}?{urlencode(query)}"
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
            raise ValueError(f"Eurostat response is not valid JSON: {exc}") from exc
        if not isinstance(data, dict) or "value" not in data or "dimension" not in data:
            raise ValueError("Eurostat response missing 'value'/'dimension' (not JSON-stat)")
        dims = data.get("id") or []
        sizes = data.get("size") or []
        if "time" not in dims:
            raise ValueError("Eurostat dataset has no 'time' dimension")

        time_pos = dims.index("time")
        time_index = data["dimension"]["time"]["category"]["index"]  # period -> ordinal
        ordinal_to_period = {ordinal: period for period, ordinal in time_index.items()}
        time_size = sizes[time_pos] if time_pos < len(sizes) else 0
        # Row-major stride for the time dimension: product of the sizes after it.
        stride = 1
        for s in sizes[time_pos + 1 :]:
            stride *= s

        observations: list[Observation] = []
        for key, value in (data.get("value") or {}).items():
            try:
                flat = int(key)
            except (TypeError, ValueError):
                continue
            time_ord = (flat // stride) % time_size if time_size else 0
            ts = _parse_period(ordinal_to_period.get(time_ord))
            if ts is None:
                continue
            observations.append(Observation(ts=ts, value=_parse_value(value)))
        observations.sort(key=lambda o: o.ts)
        return ParsedSeries(
            provider=PROVIDER,
            series_id=series_id,
            unit=_unit_label(data),
            observations=observations,
        )


def _unit_label(data: dict[str, Any]) -> str | None:
    """The human unit label from the JSON-stat ``unit`` dimension category, if present."""
    labels = data.get("dimension", {}).get("unit", {}).get("category", {}).get("label")
    if isinstance(labels, dict) and labels:
        return str(next(iter(labels.values())))
    return None


def _parse_period(value: object) -> datetime | None:
    """Map a Eurostat TIME_PERIOD (annual/quarterly/monthly) to a UTC timestamp; unknown → None."""
    if not isinstance(value, str):
        return None
    v = value.strip()
    for fmt in ("%Y-%m", "%Y-%m-%d"):
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
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
