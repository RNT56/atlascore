"""Unit/contract tests for the SEC EDGAR submissions connector (offline; respx-mocked)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from atlascore.connectors.sec.submissions import SecSubmissionsConnector
from atlascore.connectors_sdk import FetchContext, OutputKind, SafeFetcher
from atlascore.core.clock import FixedClock
from atlascore.core.errors import ConfigError

pytestmark = pytest.mark.unit

SUBMISSIONS = (
    Path(__file__).parent.parent / "fixtures" / "sec" / "apple_submissions.json"
).read_bytes()
NOW = datetime(2026, 6, 4, 12, 0, tzinfo=UTC)


def _fetcher() -> SafeFetcher:
    return SafeFetcher(
        client=httpx.Client(),
        clock=FixedClock(NOW),
        resolver=lambda _host: ["93.184.216.34"],
        sleep=lambda _s: None,
    )


def test_output_kind_is_document() -> None:
    assert SecSubmissionsConnector.output_kind is OutputKind.DOCUMENT


def test_parse_entries_golden() -> None:
    entries = SecSubmissionsConnector().parse_entries(SUBMISSIONS)
    assert len(entries) == 3
    first = entries[0]
    assert first.guid == "0000320193-26-000010"
    assert first.title == "10-K — Apple Inc."
    assert first.link == (
        "https://www.sec.gov/Archives/edgar/data/320193/000032019326000010/aapl-20260501.htm"
    )
    assert first.published == datetime(2026, 5, 1, tzinfo=UTC)
    assert first.content_html is not None
    assert "Annual report" in first.content_html
    assert "Apple Inc." in first.content_html
    # Distinct accession numbers stay distinct (dedupe key per filing).
    assert len({e.guid for e in entries}) == 3


@respx.mock
def test_fetch_builds_zero_padded_cik_url() -> None:
    route = respx.get(url__regex=r"https://data\.sec\.gov/submissions/.*").mock(
        return_value=httpx.Response(
            200, content=SUBMISSIONS, headers={"content-type": "application/json"}
        )
    )
    ctx = FetchContext(feed_url="https://data.sec.gov", params={"cik": "320193"})
    outcome = SecSubmissionsConnector().fetch(_fetcher(), ctx)
    assert outcome.status == 200
    assert "submissions/CIK0000320193.json" in str(route.calls[0].request.url)


def test_fetch_missing_cik_raises() -> None:
    with pytest.raises(ConfigError, match="cik"):
        SecSubmissionsConnector().fetch(_fetcher(), FetchContext(feed_url="https://data.sec.gov"))


def test_parse_not_json_raises() -> None:
    with pytest.raises(ValueError, match="not valid JSON"):
        SecSubmissionsConnector().parse_entries(b"<html>nope</html>")


def test_parse_missing_filings_raises() -> None:
    with pytest.raises(ValueError, match="accessionNumber"):
        SecSubmissionsConnector().parse_entries(b'{"name": "X", "cik": "1", "filings": {}}')
