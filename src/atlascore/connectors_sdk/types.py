"""Connector result types (B4 subset: fetch outcome + feed entry)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


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
