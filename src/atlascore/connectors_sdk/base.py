"""The connector interface (docs/connectors §3, §6).

A connector is the only code allowed to talk to an external source. B4 ships the fetch + parse
stages used by the RSS connector; later stages (normalize/checkpoint/healthcheck) and other
connectors land in their blocks. Connectors hold no global state and make no network calls in
``__init__`` — network I/O goes through the injected, SSRF-guarded :class:`SafeFetcher`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import ClassVar

from atlascore.connectors_sdk.fetcher import SafeFetcher
from atlascore.connectors_sdk.types import FeedEntry, FetchOutcome, ParsedSeries


class OutputKind(StrEnum):
    """What canonical store a connector's output lands in — the fetch-worker dispatch key.

    ``document`` connectors (RSS, SEC, NVD, …) implement :meth:`Connector.parse_entries` and
    feed the B5 document pipeline; ``timeseries`` connectors (FRED, …) implement
    :meth:`Connector.parse_observations` and feed ``time_series_point``.
    """

    DOCUMENT = "document"
    TIMESERIES = "timeseries"


@dataclass(frozen=True)
class FetchContext:
    """What a connector needs to fetch one source: the URL, conditional checkpoint, timeout,
    the per-host rate limit (from ``source.rate_limit``), and — for API connectors —
    connector ``params`` (e.g. FRED ``series_id``) plus the resolved ``credential`` (the secret
    named by ``credential_ref``; NEVER persisted/logged unsanitized)."""

    feed_url: str
    etag: str | None = None
    last_modified: str | None = None
    timeout: float = 30.0
    max_requests: int = 1
    window_seconds: float = 1.0
    params: dict[str, str] = field(default_factory=dict)
    credential: str | None = None


class Connector(ABC):
    """One subclass per source family. Pure stages; network only via the injected fetcher.

    A connector implements ``fetch`` plus whichever parse stage its data needs: feed/document
    connectors override :meth:`parse_entries`; time-series connectors override
    :meth:`parse_observations`. The unused one raises ``NotImplementedError`` by design.
    """

    name: ClassVar[str]
    accepted_mime_types: ClassVar[frozenset[str]]
    # Which canonical store this connector's output lands in (the fetch-worker dispatch key).
    # Defaults to ``document``; time-series connectors override it to ``timeseries``.
    output_kind: ClassVar[OutputKind] = OutputKind.DOCUMENT

    @abstractmethod
    def fetch(self, fetcher: SafeFetcher, ctx: FetchContext) -> FetchOutcome:
        """Fetch the source via the SSRF-guarded fetcher, sending conditional headers."""

    def parse_entries(self, content: bytes) -> list[FeedEntry]:
        """Parse raw bytes into feed entries (feed/document connectors override this)."""
        raise NotImplementedError(f"{type(self).__name__} does not produce feed entries")

    def parse_observations(self, content: bytes, *, series_id: str) -> ParsedSeries:
        """Parse raw bytes into a time-series for ``series_id`` (time-series connectors override).

        ``series_id`` is passed in because providers (e.g. FRED) do not echo it in the
        observations payload — the connector bundles it back into the returned series.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not produce time-series observations"
        )
