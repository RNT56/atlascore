"""Unit/contract tests for the GitHub advisories (GHSA) connector (offline; respx, no live net)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from atlascore.connectors.github.advisories import GithubAdvisoriesConnector
from atlascore.connectors_sdk import FetchContext, SafeFetcher
from atlascore.core.clock import FixedClock
from atlascore.core.errors import FetchError

pytestmark = pytest.mark.unit

_FIX = Path(__file__).parent.parent / "fixtures" / "github"
SUCCESS = (_FIX / "advisories_high_success.json").read_bytes()
EMPTY = (_FIX / "empty.json").read_bytes()
MALFORMED = (_FIX / "malformed.json").read_bytes()

NOW = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)


def _fetcher() -> SafeFetcher:
    return SafeFetcher(
        client=httpx.Client(),
        clock=FixedClock(NOW),
        resolver=lambda _host: ["140.82.112.6"],  # public -> SSRF passes
        sleep=lambda _s: None,
    )


# --- parse_entries (golden) ---


def test_parse_entries_golden() -> None:
    entries = GithubAdvisoriesConnector().parse_entries(SUCCESS)
    assert len(entries) == 2
    assert entries[0].guid == "GHSA-5m7x-g9jc-x5g7"
    assert entries[0].link == "https://github.com/advisories/GHSA-5m7x-g9jc-x5g7"
    assert entries[0].published == datetime(2026, 6, 7, 0, 31, 43, tzinfo=UTC)
    # body carries the severity + CVE marker (so a cve_watch-style rule can gate, like NVD)
    assert (
        entries[0].content_html
        and "HIGH severity advisory GHSA-5m7x-g9jc-x5g7" in entries[0].content_html
    )
    assert "CVE-2026-26422" in entries[0].content_html


def test_parse_empty_list() -> None:
    assert GithubAdvisoriesConnector().parse_entries(EMPTY) == []


def test_parse_malformed_raises() -> None:
    with pytest.raises(ValueError, match="not valid JSON"):
        GithubAdvisoriesConnector().parse_entries(MALFORMED)


def test_parse_non_list_raises() -> None:
    with pytest.raises(ValueError, match="not a list"):
        GithubAdvisoriesConnector().parse_entries(b'{"message":"x"}')


# --- fetch (contract via respx) ---


@respx.mock
def test_fetch_builds_url_with_filters() -> None:
    route = respx.get(url__regex=r"https://api\.github\.com/advisories.*").mock(
        return_value=httpx.Response(
            200, content=SUCCESS, headers={"content-type": "application/json"}
        )
    )
    ctx = FetchContext(
        feed_url="https://api.github.com",
        params={"severity": "high", "ecosystem": "pip", "per_page": "10"},
    )
    outcome = GithubAdvisoriesConnector().fetch(_fetcher(), ctx)
    assert outcome.status == 200
    requested = str(route.calls[0].request.url)
    assert "/advisories" in requested
    assert "severity=high" in requested
    assert "ecosystem=pip" in requested
    assert route.calls[0].request.headers.get("Accept") == "application/vnd.github+json"


@respx.mock
def test_fetch_rejects_wrong_mime() -> None:
    respx.get(url__regex=r"https://api\.github\.com/.*").mock(
        return_value=httpx.Response(200, content=b"<html/>", headers={"content-type": "text/html"})
    )
    ctx = FetchContext(feed_url="https://api.github.com", params={"severity": "high"})
    with pytest.raises(FetchError, match="MIME"):
        GithubAdvisoriesConnector().fetch(_fetcher(), ctx)
