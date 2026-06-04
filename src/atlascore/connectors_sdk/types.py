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
    """A single feed item, identified by its stable GUID (for dedupe)."""

    guid: str
    link: str | None = None
    title: str | None = None
    published: datetime | None = None
    summary: str | None = None
