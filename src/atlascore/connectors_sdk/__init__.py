"""Connector SDK: the connector interface, the SSRF-guarded fetcher, and result types."""

from __future__ import annotations

from atlascore.connectors_sdk.base import Connector, FetchContext, OutputKind
from atlascore.connectors_sdk.fetcher import SafeFetcher
from atlascore.connectors_sdk.rate_limit import HostRateLimiter
from atlascore.connectors_sdk.types import FeedEntry, FetchOutcome, Observation, ParsedSeries

__all__ = [
    "Connector",
    "FeedEntry",
    "FetchContext",
    "FetchOutcome",
    "HostRateLimiter",
    "Observation",
    "OutputKind",
    "ParsedSeries",
    "SafeFetcher",
]
