"""Unit tests for SafeFetcher.extra_headers (B20; respx, no live network)."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx

from atlascore.connectors_sdk import SafeFetcher
from atlascore.core.clock import FixedClock

pytestmark = pytest.mark.unit

NOW = FixedClock(datetime(2026, 6, 1, tzinfo=UTC))
URL = "https://api.example.test/data"


def _fetcher() -> SafeFetcher:
    return SafeFetcher(
        client=httpx.Client(),
        clock=NOW,
        user_agent="AtlasCore/test",
        resolver=lambda _host: ["93.184.216.34"],
        sleep=lambda _s: None,
    )


@respx.mock
def test_extra_headers_are_sent() -> None:
    route = respx.get(URL).mock(
        return_value=httpx.Response(
            200, content=b"{}", headers={"content-type": "application/json"}
        )
    )
    _fetcher().fetch(
        URL,
        extra_headers={"apiKey": "secret-key-value"},
        accept_mime=frozenset({"application/json"}),
    )
    sent = route.calls.last.request
    assert sent.headers["apiKey"] == "secret-key-value"
    assert sent.headers["User-Agent"] == "AtlasCore/test"  # managed header intact


@respx.mock
def test_extra_headers_cannot_clobber_managed() -> None:
    route = respx.get(URL).mock(
        return_value=httpx.Response(
            200, content=b"{}", headers={"content-type": "application/json"}
        )
    )
    # A caller (or a poisoned param) cannot override the project UA or the conditional cache headers.
    _fetcher().fetch(
        URL,
        etag='"v1"',
        extra_headers={"User-Agent": "evil/1.0", "If-None-Match": '"forged"'},
        accept_mime=frozenset({"application/json"}),
    )
    sent = route.calls.last.request
    assert sent.headers["User-Agent"] == "AtlasCore/test"  # setdefault wins
    assert sent.headers["If-None-Match"] == '"v1"'  # managed conditional header preserved


@respx.mock
def test_extra_headers_reapplied_across_redirect() -> None:
    respx.get(URL).mock(
        return_value=httpx.Response(302, headers={"location": "https://api.example.test/final"})
    )
    final = respx.get("https://api.example.test/final").mock(
        return_value=httpx.Response(
            200, content=b"{}", headers={"content-type": "application/json"}
        )
    )
    _fetcher().fetch(
        URL, extra_headers={"apiKey": "k"}, accept_mime=frozenset({"application/json"})
    )
    assert final.calls.last.request.headers["apiKey"] == "k"  # header survives the hop
