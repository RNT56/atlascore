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
    return SafeFetcher(client=httpx.Client(), resolver=resolver, pin_connections=False)


@pytest.mark.security
def test_https_connection_is_pinned_with_host_and_sni_preserved() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            content=b"ok",
            headers={"content-type": "text/plain"},
            request=request,
        )

    with httpx.Client(transport=httpx.MockTransport(handler), trust_env=False) as client:
        out = SafeFetcher(
            client=client,
            resolver=lambda _host: ["93.184.216.34"],
        ).fetch("https://feed.test:8443/path?q=1", extra_headers={"Host": "evil.test"})

    assert out.final_url == "https://feed.test:8443/path?q=1"
    assert seen[0].url == httpx.URL("https://93.184.216.34:8443/path?q=1")
    assert seen[0].headers["Host"] == "feed.test:8443"
    assert seen[0].extensions["sni_hostname"] == "feed.test"


@pytest.mark.security
def test_pinned_connection_falls_back_across_validated_addresses() -> None:
    attempted: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempted.append(request.url.host)
        if request.url.host == "93.184.216.34":
            raise httpx.ConnectError("unreachable", request=request)
        return httpx.Response(200, content=b"ok", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler), trust_env=False) as client:
        out = SafeFetcher(
            client=client,
            resolver=lambda _host: ["93.184.216.34", "8.8.8.8"],
        ).fetch("https://feed.test/path")

    assert out.content == b"ok"
    assert attempted == ["93.184.216.34", "8.8.8.8"]


@pytest.mark.security
def test_pinned_relative_redirect_revalidates_public_url() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/final"}, request=request)
        return httpx.Response(200, content=b"done", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler), trust_env=False) as client:
        out = SafeFetcher(
            client=client,
            resolver=lambda _host: ["93.184.216.34"],
        ).fetch("https://feed.test/start")

    assert out.final_url == "https://feed.test/final"
    assert [request.url.path for request in seen] == ["/start", "/final"]
    assert all(request.headers["Host"] == "feed.test" for request in seen)


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
