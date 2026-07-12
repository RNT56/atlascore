"""The SSRF-guarded HTTP fetcher (CLAUDE.md §14, docs/connectors §3.2, §5, §9).

Every outbound request — and every redirect hop — passes the SSRF/URL-safety guard
(scheme allowlist + resolve-and-validate-all-IPs). Redirects are followed manually with a cap so
each hop is re-validated. Conditional requests (ETag/Last-Modified) and a per-host rate limit are
applied.

The response body is **streamed** and the size cap is enforced **incrementally** on the decoded
bytes (`response.iter_bytes()`), aborting the moment the running total exceeds `max_bytes`, so a
small compressed payload that expands hugely (a decompression bomb) cannot be fully materialized
in RAM — memory is bounded to roughly the cap plus one decode chunk. A `Content-Length` header
over the cap is rejected before the body is read, and the MIME allowlist is checked from the
headers before streaming, so a wrong-MIME or oversized body is never downloaded.

The connection is pinned without private transport APIs: the request URL is rewritten to one of the
validated literal IPs, the original authority is retained in the ``Host`` header, and HTTPS passes
the original hostname through HTTPX's public ``sni_hostname`` extension for certificate verification.
Every redirect is resolved against and re-validates the public URL, never the literal-IP transport URL.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx

from atlascore.connectors_sdk.rate_limit import HostRateLimiter
from atlascore.connectors_sdk.types import FetchOutcome
from atlascore.core.clock import Clock, SystemClock
from atlascore.core.errors import FetchError
from atlascore.security.url_safety import Resolver, assert_url_allowed

DEFAULT_MAX_BYTES = 16 * 1024 * 1024  # 16 MiB cap per payload
DEFAULT_MAX_REDIRECTS = 5
# Explicit redirect codes — deliberately NOT 304 (Not Modified is handled separately; some
# httpx versions report 304 as `is_redirect`, which it is not for our purposes).
_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})


class SafeFetcher:
    """An httpx-backed fetcher that enforces SSRF safety, conditional requests, and rate limits."""

    def __init__(
        self,
        *,
        client: httpx.Client,
        rate_limiter: HostRateLimiter | None = None,
        clock: Clock | None = None,
        resolver: Resolver | None = None,
        user_agent: str = "AtlasCore/0.1.0",
        sleep: Callable[[float], None] = time.sleep,
        max_redirects: int = DEFAULT_MAX_REDIRECTS,
        pin_connections: bool = True,
    ) -> None:
        self._client = client
        self._rate_limiter = rate_limiter or HostRateLimiter()
        self._clock = clock or SystemClock()
        self._resolver = resolver
        self._user_agent = user_agent
        self._sleep = sleep
        self._max_redirects = max_redirects
        self._pin_connections = pin_connections

    def fetch(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
        timeout: float = 30.0,
        max_requests: int = 1,
        window_seconds: float = 1.0,
        accept_mime: frozenset[str] | None = None,
        max_bytes: int = DEFAULT_MAX_BYTES,
        extra_headers: dict[str, str] | None = None,
    ) -> FetchOutcome:
        """Fetch ``url`` with conditional headers, SSRF-validating every hop. Raises
        :class:`SsrfError` on a disallowed target and :class:`FetchError` on a bad
        status / MIME / size or too many redirects.

        ``extra_headers`` (e.g. an ``apiKey`` auth header, B20) are merged via ``setdefault`` — they
        can NEVER override the managed ``User-Agent``/conditional cache headers (a poisoned param
        can't strip the project UA or rewrite the ETag cache). They are retained only across
        same-origin redirects; cross-origin hops never receive source credentials."""
        current = url
        initial_origin = _origin(url)
        for _hop in range(self._max_redirects + 1):
            target = assert_url_allowed(current, resolver=self._resolver)  # scheme + IP validation
            wait = self._rate_limiter.acquire(
                target.host,
                now=self._clock.now(),
                max_requests=max_requests,
                window_seconds=window_seconds,
            )
            if wait > 0:
                self._sleep(wait)
            headers = {"User-Agent": self._user_agent}
            if etag:
                headers["If-None-Match"] = etag
            if last_modified:
                headers["If-Modified-Since"] = last_modified
            if extra_headers and _origin(current) == initial_origin:
                # setdefault: callers add auth/custom headers but can't clobber the managed ones.
                for key, value in extra_headers.items():
                    headers.setdefault(key, value)
            addresses: tuple[str | None, ...] = (
                tuple(dict.fromkeys(target.ips)) if self._pin_connections else (None,)
            )
            redirect: str | None = None
            last_connect_error: httpx.ConnectError | httpx.ConnectTimeout | None = None
            for address in addresses:
                request_url = current
                extensions: dict[str, str] = {}
                request_headers = dict(headers)
                if address is not None:
                    request_url = str(httpx.URL(current).copy_with(host=address))
                    request_headers["Host"] = _authority(target.scheme, target.host, target.port)
                    if target.scheme == "https":
                        extensions["sni_hostname"] = target.host
                try:
                    # Stream so the body is read (and decoded) incrementally under the size cap.
                    with self._client.stream(
                        "GET",
                        request_url,
                        headers=request_headers,
                        extensions=extensions,
                        follow_redirects=False,
                        timeout=timeout,
                    ) as response:
                        if response.status_code in _REDIRECT_CODES:
                            location = response.headers.get("location")
                            if not location:
                                raise FetchError(f"redirect with no Location from {current!r}")
                            # Resolve relative redirects against the public URL. ``response.url`` is
                            # the literal-IP transport URL when pinning is enabled.
                            redirect = str(httpx.URL(current).join(location))
                            break
                        return self._finalize(
                            response,
                            accept_mime=accept_mime,
                            max_bytes=max_bytes,
                            final_url=current,
                        )
                except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                    last_connect_error = exc
            if redirect is not None:
                current = redirect
                continue
            if last_connect_error is not None:
                raise last_connect_error
        raise FetchError(f"too many redirects (> {self._max_redirects}) starting at {url!r}")

    def _finalize(
        self,
        response: httpx.Response,
        *,
        accept_mime: frozenset[str] | None,
        max_bytes: int,
        final_url: str,
    ) -> FetchOutcome:
        if response.status_code == 304:
            return FetchOutcome(
                status=304,
                not_modified=True,
                content=b"",
                mime_type=None,
                etag=response.headers.get("etag"),
                last_modified=response.headers.get("last-modified"),
                final_url=final_url,
            )
        if response.status_code >= 400 or response.status_code < 200:
            raise FetchError(f"HTTP {response.status_code} from {response.url}")
        # Reject an over-cap Content-Length before reading the body (cheap early-out; the
        # header is the *encoded* size, so it only catches uncompressed oversized bodies).
        declared = response.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > max_bytes:
            raise FetchError(f"Content-Length {declared} exceeds cap {max_bytes}")
        # Check MIME from headers BEFORE streaming, so a wrong-MIME body is never downloaded.
        mime = response.headers.get("content-type", "").split(";")[0].strip().lower() or None
        if accept_mime is not None and mime not in accept_mime:
            raise FetchError(f"MIME {mime!r} not in allowlist {sorted(accept_mime)}")
        # Stream the decoded body, aborting the moment the running total exceeds the cap, so a
        # decompression bomb can never be fully materialized in memory.
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_bytes():
            total += len(chunk)
            if total > max_bytes:
                raise FetchError(f"payload exceeds cap {max_bytes} bytes (aborted mid-stream)")
            chunks.append(chunk)
        return FetchOutcome(
            status=response.status_code,
            not_modified=False,
            content=b"".join(chunks),
            mime_type=mime,
            etag=response.headers.get("etag"),
            last_modified=response.headers.get("last-modified"),
            final_url=final_url,
        )


def _origin(url: str) -> tuple[str, str, int]:
    parsed = httpx.URL(url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return parsed.scheme, parsed.host, port


def _authority(scheme: str, host: str, port: int) -> str:
    rendered_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    default = 443 if scheme == "https" else 80
    return rendered_host if port == default else f"{rendered_host}:{port}"
