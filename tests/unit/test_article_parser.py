"""Unit + golden tests for the article parser (docs/connectors §3.4, §8.2). No network.

The RSS path (feedparser-sanitized HTML -> ``strip_html``) is fully deterministic, so its
output is pinned byte-for-byte (the per-source golden). The full-HTML-page path delegates to
trafilatura (version-pinned via uv.lock); it is asserted behaviorally (main text present,
active content absent).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from atlascore.connectors.rss import RssConnector
from atlascore.parsers.article import PARSER_VERSION, ArticleParser

pytestmark = pytest.mark.unit

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "rss" / "sample_press.xml"

# Golden output for the fabricated sample feed. Pin via review when parser_version changes.
_GOLDEN = {
    "https://example.test/press/a": {
        "title": "Policy statement & rate decision",
        "canonical_url": "https://example.test/press/a",
        "published_at": datetime(2026, 6, 3, 18, 0, 0, tzinfo=UTC),
        "body_text": "The committee decided to hold rates steady.\nRead more »",
    },
    "https://example.test/press/b": {
        "title": "Speech on financial stability",
        "canonical_url": "https://example.test/press/b",
        "published_at": datetime(2026, 6, 4, 14, 30, 0, tzinfo=UTC),
        "body_text": "Remarks on the economic outlook and the path of policy.",
    },
}


def test_rss_golden_output() -> None:
    """Each sample feed item parses to its exact pinned canonical document fields."""
    entries = RssConnector().parse_entries(_FIXTURE.read_bytes())
    parser = ArticleParser()
    assert parser.version == PARSER_VERSION
    by_guid = {e.guid: e for e in entries}
    assert set(by_guid) == set(_GOLDEN)
    for guid, expected in _GOLDEN.items():
        entry = by_guid[guid]
        parsed = parser.parse(
            entry.content_html or entry.summary,
            fallback_title=entry.title,
            fallback_url=entry.link,
            fallback_published=entry.published,
        )
        assert parsed.title == expected["title"]
        assert parsed.canonical_url == expected["canonical_url"]
        assert parsed.published_at == expected["published_at"]
        assert parsed.body_text == expected["body_text"]
        assert parsed.language is None  # detection is enrichment's job (B11)


def test_hostile_fragment_is_sanitized() -> None:
    parsed = ArticleParser().parse("<p>real text here</p><script>steal()</script>")
    assert "real text here" in parsed.body_text
    assert "steal" not in parsed.body_text


def test_empty_html_yields_empty_body() -> None:
    parsed = ArticleParser().parse("", fallback_title="t", fallback_url="https://x.test/a")
    assert parsed.body_text == ""
    assert parsed.title == "t"
    assert parsed.canonical_url == "https://x.test/a"


def test_non_http_url_is_dropped() -> None:
    parsed = ArticleParser().parse("<p>x</p>", fallback_url="ftp://x.test/a")
    assert parsed.canonical_url is None


def test_fallback_title_takes_precedence_over_html_title() -> None:
    parsed = ArticleParser().parse(
        "<html><head><title>HTML Title</title></head><body><p>body</p></body></html>",
        fallback_title="Feed Title",
    )
    assert parsed.title == "Feed Title"


def test_html_title_used_when_no_fallback() -> None:
    parsed = ArticleParser().parse(
        "<html><head><title>HTML &amp; Title</title></head><body><p>body</p></body></html>"
    )
    assert parsed.title == "HTML & Title"


def test_hostile_feed_title_is_sanitized() -> None:
    parsed = ArticleParser().parse(
        "<p>body</p>", fallback_title="<script>alert(1)</script>Breaking News"
    )
    assert "script" not in (parsed.title or "").lower()
    assert "alert" not in (parsed.title or "")
    assert "Breaking News" in (parsed.title or "")


def test_full_page_extraction_drops_boilerplate_and_scripts() -> None:
    """trafilatura path: a full article page yields the main text, no nav/script."""
    html = (
        "<html><head><title>Article</title></head><body>"
        "<nav>Home About Contact Subscribe Login Menu</nav>"
        "<article><h1>Quarterly Review</h1>"
        "<p>The central bank held its benchmark rate unchanged this quarter, citing "
        "balanced risks to the outlook and continued moderation in price pressures.</p>"
        "<p>Officials signalled patience, noting that incoming data would guide the pace "
        "of any future adjustments to the policy stance over the coming months.</p>"
        "</article><script>track()</script>"
        "<footer>Copyright 2026 All rights reserved Terms Privacy</footer>"
        "</body></html>"
    )
    parsed = ArticleParser().parse(html)
    assert "benchmark rate unchanged" in parsed.body_text
    assert "track()" not in parsed.body_text
    assert "Copyright" not in parsed.body_text
