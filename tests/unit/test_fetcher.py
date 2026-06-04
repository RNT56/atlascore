"""Contract tests for the SSRF-guarded fetcher (respx-mocked HTTP; no live network)."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest
import respx

from atlascore.connectors_sdk import SafeFetcher
from atlascore.core.errors import FetchError, SsrfError

pytestmark = pytest.mark.contract

RSS_MIME = frozenset({"application/rss+xml", "application/xml"})


def _public_resolver(_host: str) -> list[str]:
    return ["93.184.216.34"]


def _fetcher(resolver: Callable[[str], list[str]] = _public_resolver) -> SafeFetcher:
    return SafeFetcher(client=httpx.Client(), resolver=resolver)


@respx.mock
def test_fetch_success_returns_content_and_validators() -> None:
    respx.get("https://feed.test/rss").mock(
        return_value=httpx.Response(
            200,
            content=b"<rss/>",
            headers={"content-type": "application/rss+xml", "etag": '"v1"'},
        )
    )
    out = _fetcher().fetch("https://feed.test/rss", accept_mime=RSS_MIME)
    assert out.status == 200
    assert out.not_modified is False
    assert out.content == b"<rss/>"
    assert out.mime_type == "application/rss+xml"
    assert out.etag == '"v1"'


@respx.mock
def test_304_returns_not_modified() -> None:
    respx.get("https://feed.test/rss").mock(return_value=httpx.Response(304))
    out = _fetcher().fetch("https://feed.test/rss", etag='"v1"')
    assert out.not_modified is True
    assert out.content == b""


@respx.mock
def test_follows_and_revalidates_redirect() -> None:
    respx.get("https://a.test/feed").mock(
        return_value=httpx.Response(302, headers={"location": "https://b.test/feed"})
    )
    respx.get("https://b.test/feed").mock(
        return_value=httpx.Response(200, content=b"ok", headers={"content-type": "application/xml"})
    )
    out = _fetcher().fetch("https://a.test/feed")
    assert out.content == b"ok"
    assert out.final_url == "https://b.test/feed"


@respx.mock
def test_redirect_to_private_host_is_blocked() -> None:
    respx.get("https://a.test/feed").mock(
        return_value=httpx.Response(302, headers={"location": "https://evil.test/x"})
    )

    def resolver(host: str) -> list[str]:
        return ["10.0.0.1"] if host == "evil.test" else ["93.184.216.34"]

    with pytest.raises(SsrfError):
        _fetcher(resolver).fetch("https://a.test/feed")


@respx.mock
def test_private_target_is_blocked_before_request() -> None:
    route = respx.get("https://internal.test/").mock(return_value=httpx.Response(200))
    with pytest.raises(SsrfError):
        _fetcher(lambda _h: ["10.0.0.1"]).fetch("https://internal.test/")
    assert not route.called  # never connected


@respx.mock
def test_http_error_raises_fetch_error() -> None:
    respx.get("https://feed.test/rss").mock(return_value=httpx.Response(500))
    with pytest.raises(FetchError, match="500"):
        _fetcher().fetch("https://feed.test/rss")


@respx.mock
def test_rate_limited_429_raises_fetch_error() -> None:
    # The job will retry via the queue's backoff; the fetcher surfaces the 429 as a FetchError.
    respx.get("https://feed.test/rss").mock(
        return_value=httpx.Response(429, headers={"retry-after": "5"})
    )
    with pytest.raises(FetchError, match="429"):
        _fetcher().fetch("https://feed.test/rss")


@respx.mock
def test_disallowed_mime_raises() -> None:
    respx.get("https://feed.test/rss").mock(
        return_value=httpx.Response(200, content=b"x", headers={"content-type": "text/plain"})
    )
    with pytest.raises(FetchError, match="MIME"):
        _fetcher().fetch("https://feed.test/rss", accept_mime=RSS_MIME)


@respx.mock
def test_oversized_payload_raises() -> None:
    respx.get("https://feed.test/rss").mock(
        return_value=httpx.Response(
            200, content=b"x" * 2048, headers={"content-type": "application/xml"}
        )
    )
    with pytest.raises(FetchError, match="exceeds cap"):
        _fetcher().fetch("https://feed.test/rss", max_bytes=1024)


@respx.mock
def test_decompression_bomb_is_aborted_mid_stream() -> None:
    # 84 compressed bytes decode to 50 KB; Content-Length (compressed) is under the cap, so only
    # the incremental DECODED-byte cap catches it — proving the body is never fully materialized.
    import gzip

    bomb = gzip.compress(b"A" * 50_000)
    respx.get("https://feed.test/rss").mock(
        return_value=httpx.Response(
            200,
            content=bomb,
            headers={"content-type": "application/xml", "content-encoding": "gzip"},
        )
    )
    with pytest.raises(FetchError, match="aborted mid-stream"):
        _fetcher().fetch("https://feed.test/rss", max_bytes=1024)


@respx.mock
def test_oversized_content_length_rejected_before_body() -> None:
    route = respx.get("https://feed.test/rss").mock(
        return_value=httpx.Response(
            200,
            content=b"x" * 8,
            headers={"content-type": "application/xml", "content-length": "999999"},
        )
    )
    with pytest.raises(FetchError, match="Content-Length"):
        _fetcher().fetch("https://feed.test/rss", max_bytes=1024)
    assert route.called
