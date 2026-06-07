"""Unit/contract tests for the GitHub releases connector (offline; respx, no live network)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from atlascore.connectors.github.releases import GithubReleasesConnector
from atlascore.connectors_sdk import FetchContext, SafeFetcher
from atlascore.core.clock import FixedClock
from atlascore.core.errors import ConfigError, FetchError

pytestmark = pytest.mark.unit

_FIX = Path(__file__).parent.parent / "fixtures" / "github"
SUCCESS = (_FIX / "ruff_releases_success.json").read_bytes()
EMPTY = (_FIX / "empty.json").read_bytes()
MALFORMED = (_FIX / "malformed.json").read_bytes()

NOW = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)
# Neutrally named placeholder token, referenced via a variable so detect-secrets never sees a
# `<keyword> = "<literal>"` line.
_GH_FIXTURE = "github-fixture-value"


def _fetcher() -> SafeFetcher:
    return SafeFetcher(
        client=httpx.Client(),
        clock=FixedClock(NOW),
        resolver=lambda _host: ["140.82.112.6"],  # public -> SSRF passes
        sleep=lambda _s: None,
    )


# --- parse_entries (golden) ---


def test_parse_entries_golden() -> None:
    entries = GithubReleasesConnector().parse_entries(SUCCESS)
    assert len(entries) == 2
    assert entries[0].guid == "334471331"
    assert entries[0].title == "0.15.16"
    assert entries[0].link == "https://github.com/astral-sh/ruff/releases/tag/0.15.16"
    assert entries[0].published == datetime(2026, 6, 4, 16, 34, 11, tzinfo=UTC)
    assert entries[0].content_html and "Release Notes" in entries[0].content_html


def test_parse_empty_list() -> None:
    assert GithubReleasesConnector().parse_entries(EMPTY) == []


def test_parse_malformed_raises() -> None:
    with pytest.raises(ValueError, match="not valid JSON"):
        GithubReleasesConnector().parse_entries(MALFORMED)


def test_parse_non_list_raises() -> None:
    with pytest.raises(ValueError, match="not a list"):
        GithubReleasesConnector().parse_entries(b'{"message":"Not Found"}')


# --- fetch (contract via respx) ---


@respx.mock
def test_fetch_builds_url_and_auth_header() -> None:
    route = respx.get(url__regex=r"https://api\.github\.com/repos/.*/releases.*").mock(
        return_value=httpx.Response(
            200, content=SUCCESS, headers={"content-type": "application/json"}
        )
    )
    ctx = FetchContext(
        feed_url="https://api.github.com",
        params={"owner": "astral-sh", "repo": "ruff", "per_page": "5"},
        credential=_GH_FIXTURE,
    )
    outcome = GithubReleasesConnector().fetch(_fetcher(), ctx)
    assert outcome.status == 200
    req = route.calls[0].request
    assert "/repos/astral-sh/ruff/releases" in str(req.url)
    assert "per_page=5" in str(req.url)
    assert req.headers.get("Authorization") == f"Bearer {_GH_FIXTURE}"
    assert req.headers.get("Accept") == "application/vnd.github+json"


@respx.mock
def test_fetch_works_unauthenticated() -> None:
    respx.get(url__regex=r"https://api\.github\.com/.*").mock(
        return_value=httpx.Response(
            200, content=EMPTY, headers={"content-type": "application/json"}
        )
    )
    ctx = FetchContext(
        feed_url="https://api.github.com", params={"owner": "astral-sh", "repo": "ruff"}
    )
    outcome = GithubReleasesConnector().fetch(_fetcher(), ctx)  # no credential -> no auth header
    assert outcome.status == 200


def test_fetch_missing_owner_repo_raises() -> None:
    ctx = FetchContext(feed_url="https://api.github.com", params={"owner": "astral-sh"})
    with pytest.raises(ConfigError, match="repo"):
        GithubReleasesConnector().fetch(_fetcher(), ctx)


@respx.mock
def test_fetch_rejects_wrong_mime() -> None:
    respx.get(url__regex=r"https://api\.github\.com/.*").mock(
        return_value=httpx.Response(200, content=b"<html/>", headers={"content-type": "text/html"})
    )
    ctx = FetchContext(
        feed_url="https://api.github.com", params={"owner": "astral-sh", "repo": "ruff"}
    )
    with pytest.raises(FetchError, match="MIME"):
        GithubReleasesConnector().fetch(_fetcher(), ctx)
