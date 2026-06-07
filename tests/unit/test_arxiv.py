"""Unit/contract tests for the arXiv connector (offline; respx-mocked HTTP, no live network)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from atlascore.connectors.arxiv.papers import ArxivConnector
from atlascore.connectors_sdk import FetchContext, SafeFetcher
from atlascore.core.clock import FixedClock
from atlascore.core.errors import ConfigError, FetchError

pytestmark = pytest.mark.unit

_FIX = Path(__file__).parent.parent / "fixtures" / "arxiv"
SUCCESS = (_FIX / "cs_lg_success.xml").read_bytes()
EMPTY = (_FIX / "empty.xml").read_bytes()
MALFORMED = (_FIX / "malformed.xml").read_bytes()

NOW = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)


def _fetcher() -> SafeFetcher:
    return SafeFetcher(
        client=httpx.Client(),
        clock=FixedClock(NOW),
        resolver=lambda _host: ["93.184.216.34"],  # public -> SSRF passes
        sleep=lambda _s: None,
    )


# --- parse_entries (golden; inherited feedparser parsing) ---


def test_parse_entries_golden() -> None:
    entries = ArxivConnector().parse_entries(SUCCESS)
    assert len(entries) == 2
    assert entries[0].guid == "http://arxiv.org/abs/2606.06494v1"
    assert all(e.guid.startswith("http://arxiv.org/abs/") for e in entries)
    assert entries[0].title and "TailLoR" in entries[0].title
    assert entries[0].content_html  # the abstract becomes the document body
    assert entries[0].published is not None


def test_parse_empty_feed() -> None:
    assert ArxivConnector().parse_entries(EMPTY) == []


def test_parse_malformed_raises() -> None:
    with pytest.raises(ValueError, match="unparseable"):
        ArxivConnector().parse_entries(MALFORMED)


# --- fetch (contract via respx) ---


@respx.mock
def test_fetch_builds_query_url() -> None:
    route = respx.get(url__regex=r"https://export\.arxiv\.org/api/query.*").mock(
        return_value=httpx.Response(
            200, content=SUCCESS, headers={"content-type": "application/atom+xml"}
        )
    )
    ctx = FetchContext(
        feed_url="https://export.arxiv.org",
        params={"search_query": "cat:cs.LG", "max_results": "25"},
    )
    outcome = ArxivConnector().fetch(_fetcher(), ctx)
    assert outcome.status == 200
    requested = str(route.calls[0].request.url)
    assert "/api/query" in requested
    assert "search_query=cat%3Acs.LG" in requested or "search_query=cat:cs.LG" in requested
    assert "max_results=25" in requested
    assert "sortBy=submittedDate" in requested


def test_fetch_missing_search_query_raises() -> None:
    ctx = FetchContext(feed_url="https://export.arxiv.org")
    with pytest.raises(ConfigError, match="search_query"):
        ArxivConnector().fetch(_fetcher(), ctx)


@respx.mock
def test_fetch_rejects_wrong_mime() -> None:
    respx.get(url__regex=r"https://export\.arxiv\.org/.*").mock(
        return_value=httpx.Response(
            200, content=b"{}", headers={"content-type": "application/json"}
        )
    )
    ctx = FetchContext(feed_url="https://export.arxiv.org", params={"search_query": "cat:cs.LG"})
    with pytest.raises(FetchError, match="MIME"):
        ArxivConnector().fetch(_fetcher(), ctx)
