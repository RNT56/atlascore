"""Unit tests for RSS/Atom entry parsing (pure; no network)."""

from __future__ import annotations

import pytest

from atlascore.connectors.rss import RssConnector

pytestmark = pytest.mark.unit

RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>Test feed</title>
  <item><title>A</title><guid>guid-a</guid><link>https://x.test/a</link>
        <pubDate>Tue, 03 Jun 2026 10:00:00 GMT</pubDate></item>
  <item><title>B</title><guid>guid-b</guid><link>https://x.test/b</link></item>
</channel></rss>"""

EMPTY = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>Empty</title></channel></rss>"""

NO_ID = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>T</title><item><title>no id or link</title></item></channel></rss>"""

# Non-feed content: feedparser sets bozo and recovers no entries (it tolerates merely-truncated
# XML by recovering partial entries, so we use clearly-non-feed bytes here).
MALFORMED = b"this is just plain text, definitely not an RSS or Atom feed"


def test_parses_entries_with_guids() -> None:
    entries = RssConnector().parse_entries(RSS)
    assert {e.guid for e in entries} == {"guid-a", "guid-b"}
    first = next(e for e in entries if e.guid == "guid-a")
    assert first.title == "A"
    assert first.link == "https://x.test/a"
    assert first.published is not None


def test_empty_feed_yields_no_entries() -> None:
    assert RssConnector().parse_entries(EMPTY) == []


def test_entry_without_stable_id_is_skipped() -> None:
    assert RssConnector().parse_entries(NO_ID) == []


def test_malformed_feed_raises() -> None:
    with pytest.raises(ValueError, match="unparseable"):
        RssConnector().parse_entries(MALFORMED)
