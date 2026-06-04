"""Connector result types: fetch outcome + feed entry (B4) + time-series observations (B9)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class FetchOutcome:
    """The result of one HTTP fetch (after SSRF + status/MIME/size validation)."""

    status: int
    not_modified: bool  # True on HTTP 304 (no new content)
    content: bytes  # raw payload (empty when not_modified)
    mime_type: str | None
    etag: str | None
    last_modified: str | None
    final_url: str  # after following redirects


@dataclass(frozen=True)
class FeedEntry:
    """A single feed item, identified by its stable GUID (for dedupe).

    ``content_html`` is the richest body the feed offers (``content:encoded`` / Atom
    ``<content>``, else ``<description>``/``summary``) — the article parser (B5) turns it
    into the canonical ``document`` body. May be HTML; treat as untrusted (sanitized at parse).
    """

    guid: str
    link: str | None = None
    title: str | None = None
    published: datetime | None = None
    summary: str | None = None
    content_html: str | None = None


@dataclass(frozen=True)
class Observation:
    """One scalar time-series point (macro ``kind=observation``).

    ``value`` is a :class:`~decimal.Decimal` (or ``None`` for a missing/gap value — never
    zero-filled) so ``numeric`` precision is preserved end to end. ``vintage`` is the data
    as-of date for a revisable series (``None`` = latest).
    """

    ts: datetime
    value: Decimal | None
    vintage: datetime | None = None


@dataclass(frozen=True)
class ParsedSeries:
    """A parsed time-series: provider + series id + unit + ordered observations."""

    provider: str
    series_id: str
    unit: str | None
    observations: list[Observation]
