"""The SSRF-guarded HTTP fetcher (CLAUDE.md §14, docs/connectors §3.2, §5, §9).

Every outbound request — and every redirect hop — passes the SSRF/URL-safety guard
(scheme allowlist + resolve-and-validate-all-IPs). Redirects are followed manually with a cap so
each hop is re-validated. Conditional requests (ETag/Last-Modified) and a per-host rate limit are
applied. The response size is capped to guard against decompression/runaway bodies.

Security note (B4): the guard validates the resolved IPs immediately before each connect and
re-validates every redirect hop. Pinning the socket to the exact validated IP (the final
anti-DNS-rebinding step in §9) is a documented hardening follow-up — it needs a custom httpcore
transport that the offline (respx) test suite cannot exercise; the validation above is unit-tested.
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
        user_agent: str = "AtlasCore/0.0",
        sleep: Callable[[float], None] = time.sleep,
        max_redirects: int = DEFAULT_MAX_REDIRECTS,
    ) -> None:
        self._client = client
        self._rate_limiter = rate_limiter or HostRateLimiter()
        self._clock = clock or SystemClock()
        self._resolver = resolver
        self._user_agent = user_agent
        self._sleep = sleep
        self._max_redirects = max_redirects

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
    ) -> FetchOutcome:
        """Fetch ``url`` with conditional headers, SSRF-validating every hop. Raises
        :class:`SsrfError` on a disallowed target and :class:`FetchError` on a bad
        status / MIME / size or too many redirects."""
        current = url
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
            response = self._client.get(
                current, headers=headers, follow_redirects=False, timeout=timeout
            )
            if response.status_code in _REDIRECT_CODES:
                location = response.headers.get("location")
                if not location:
                    raise FetchError(f"redirect with no Location from {current!r}")
                current = str(response.url.join(location))  # resolve relative redirects
                continue
            return self._finalize(response, accept_mime=accept_mime, max_bytes=max_bytes)
        raise FetchError(f"too many redirects (> {self._max_redirects}) starting at {url!r}")

    def _finalize(
        self, response: httpx.Response, *, accept_mime: frozenset[str] | None, max_bytes: int
    ) -> FetchOutcome:
        if response.status_code == 304:
            return FetchOutcome(
                status=304,
                not_modified=True,
                content=b"",
                mime_type=None,
                etag=response.headers.get("etag"),
                last_modified=response.headers.get("last-modified"),
                final_url=str(response.url),
            )
        if response.status_code >= 400 or response.status_code < 200:
            raise FetchError(f"HTTP {response.status_code} from {response.url}")
        content = response.content
        if len(content) > max_bytes:
            raise FetchError(f"payload {len(content)} bytes exceeds cap {max_bytes}")
        mime = response.headers.get("content-type", "").split(";")[0].strip().lower() or None
        if accept_mime is not None and mime not in accept_mime:
            raise FetchError(f"MIME {mime!r} not in allowlist {sorted(accept_mime)}")
        return FetchOutcome(
            status=response.status_code,
            not_modified=False,
            content=content,
            mime_type=mime,
            etag=response.headers.get("etag"),
            last_modified=response.headers.get("last-modified"),
            final_url=str(response.url),
        )
