"""The connector interface (docs/connectors §3, §6).

A connector is the only code allowed to talk to an external source. B4 ships the fetch + parse
stages used by the RSS connector; later stages (normalize/checkpoint/healthcheck) and other
connectors land in their blocks. Connectors hold no global state and make no network calls in
``__init__`` — network I/O goes through the injected, SSRF-guarded :class:`SafeFetcher`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar

from atlascore.connectors_sdk.fetcher import SafeFetcher
from atlascore.connectors_sdk.types import FeedEntry, FetchOutcome


@dataclass(frozen=True)
class FetchContext:
    """What a connector needs to fetch one source: the URL, conditional checkpoint, timeout,
    and the per-host rate limit (from ``source.rate_limit``)."""

    feed_url: str
    etag: str | None = None
    last_modified: str | None = None
    timeout: float = 30.0
    max_requests: int = 1
    window_seconds: float = 1.0


class Connector(ABC):
    """One subclass per source family. Pure stages; network only via the injected fetcher."""

    name: ClassVar[str]
    accepted_mime_types: ClassVar[frozenset[str]]

    @abstractmethod
    def fetch(self, fetcher: SafeFetcher, ctx: FetchContext) -> FetchOutcome:
        """Fetch the source via the SSRF-guarded fetcher, sending conditional headers."""

    @abstractmethod
    def parse_entries(self, content: bytes) -> list[FeedEntry]:
        """Parse raw bytes into feed entries (pure, deterministic, no network)."""
