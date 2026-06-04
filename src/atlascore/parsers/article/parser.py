"""The article parser: HTML -> sanitized text + attribution (docs/connectors §3.4, §7.1).

Pure and deterministic — **no network**. Reads only the bytes handed to it (which come
from the immutable raw store), so re-running the pinned ``parser_version`` against the
same raw reproduces the output (spec R2). Extraction strategy:

1. **trafilatura** (primary) — extracts the main article text from a full HTML page,
   dropping boilerplate/nav/active content. It deliberately returns ``None`` for short
   fragments (e.g. an RSS ``<description>`` snippet) and for plain text.
2. **``strip_html`` fallback** — when trafilatura declines, take the strongest-allowlist
   text (all tags removed, active content dropped). This is the path most RSS items take.

Attribution (title / canonical URL / published timestamp) is taken from the caller's
feed-level values when given (those are authoritative for RSS), with an HTML ``<title>``
fallback for full pages. Language detection is left to enrichment (B11) — it stays
``None`` here (missing data stays ``null``, CLAUDE.md §15).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

import trafilatura

from atlascore.core.logging import get_logger
from atlascore.security.sanitization import strip_html

_log = get_logger("atlascore.parsers.article")

# Pinned parser version, written onto every document.parser_version. Bump (and regenerate
# golden fixtures via an explicit reviewed change) when extraction output intentionally changes.
PARSER_VERSION = "article@1.0.0"

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class ParsedArticle:
    """The structured output of the article parser for one document."""

    title: str | None
    body_text: str
    canonical_url: str | None
    published_at: datetime | None
    language: str | None = None


def _normalize_url(url: str | None) -> str | None:
    """Keep only an http(s) URL as the canonical identifier; otherwise ``None``.

    This is an identity/dedupe key, not a fetch target — the SSRF guard runs at fetch
    time, not here.
    """
    if not url:
        return None
    candidate = url.strip()
    if candidate.lower().startswith(("http://", "https://")):
        return candidate
    return None


def _extract_main_text(html: str) -> str:
    """trafilatura main-content extraction, falling back to ``strip_html`` for fragments."""
    if not html or not html.strip():
        return ""
    try:
        extracted = trafilatura.extract(html, include_comments=False)
    except Exception as exc:  # untrusted-content boundary: never crash on hostile markup
        _log.warning("trafilatura_failed", error=str(exc))
        extracted = None
    if extracted and extracted.strip():
        return extracted.strip()
    return strip_html(html)


def _extract_title(html: str) -> str | None:
    match = _TITLE_RE.search(html)
    if match is None:
        return None
    title = strip_html(match.group(1))
    return title or None


class ArticleParser:
    """Turns one item's HTML into a :class:`ParsedArticle`. Holds no state."""

    version = PARSER_VERSION

    def parse(
        self,
        html: str | None,
        *,
        fallback_title: str | None = None,
        fallback_url: str | None = None,
        fallback_published: datetime | None = None,
    ) -> ParsedArticle:
        """Parse ``html`` into a :class:`ParsedArticle`.

        ``fallback_*`` carry the feed-level title/link/published, which are authoritative
        for RSS and take precedence; the HTML ``<title>`` is only used when no feed title
        was supplied.
        """
        body_text = _extract_main_text(html or "")
        # Sanitize the feed-supplied title too — it is persisted to document.title and shown
        # to agents as provenance (CLAUDE.md §14/§15); a hostile <title> must not pass through.
        title = strip_html(fallback_title) or _extract_title(html or "")
        return ParsedArticle(
            title=title,
            body_text=body_text,
            canonical_url=_normalize_url(fallback_url),
            published_at=fallback_published,
            language=None,
        )
