"""Generic RSS/Atom connector (docs/connectors §7.1).

B4 scope: fetch the feed (conditional, SSRF-guarded), and parse entries enough to extract a
stable GUID for dedupe. Turning entries into canonical `document` rows (the article parser) is B5.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import feedparser

from atlascore.connectors_sdk.base import Connector, FetchContext
from atlascore.connectors_sdk.fetcher import SafeFetcher
from atlascore.connectors_sdk.types import FeedEntry, FetchOutcome


def _entry_guid(entry: Any) -> str | None:
    # feedparser maps RSS <guid> and Atom <id> to entry.id; fall back to the link.
    guid = entry.get("id") or entry.get("guid") or entry.get("link")
    return str(guid) if guid else None


def _entry_published(entry: Any) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed is None:
        return None
    # feedparser returns a time.struct_time (UTC); build an aware datetime from named fields.
    return datetime(
        parsed.tm_year,
        parsed.tm_mon,
        parsed.tm_mday,
        parsed.tm_hour,
        parsed.tm_min,
        parsed.tm_sec,
        tzinfo=UTC,
    )


class RssConnector(Connector):
    """Fetches one RSS/Atom feed and extracts deduplicable entries."""

    name = "rss"
    accepted_mime_types = frozenset(
        {
            "application/rss+xml",
            "application/atom+xml",
            "application/xml",
            "text/xml",
            "text/html",
        }
    )

    def fetch(self, fetcher: SafeFetcher, ctx: FetchContext) -> FetchOutcome:
        return fetcher.fetch(
            ctx.feed_url,
            etag=ctx.etag,
            last_modified=ctx.last_modified,
            timeout=ctx.timeout,
            max_requests=ctx.max_requests,
            window_seconds=ctx.window_seconds,
            accept_mime=self.accepted_mime_types,
        )

    def parse_entries(self, content: bytes) -> list[FeedEntry]:
        parsed = feedparser.parse(content)
        # Fail loudly on garbage (bozo + nothing parsed) so the job retries→dead rather than
        # silently succeeding; tolerate minor bozo when real entries were still recovered.
        if parsed.bozo and not parsed.entries:
            reason = getattr(parsed, "bozo_exception", "unparseable feed")
            raise ValueError(f"unparseable RSS/Atom feed: {reason}")
        entries: list[FeedEntry] = []
        for raw in parsed.entries:
            guid = _entry_guid(raw)
            if guid is None:
                continue  # no stable id → cannot dedupe; skip
            entries.append(
                FeedEntry(
                    guid=guid,
                    link=raw.get("link"),
                    title=raw.get("title"),
                    published=_entry_published(raw),
                    summary=raw.get("summary"),
                )
            )
        return entries
