"""Unit tests for NVD keyed mode + apiKey redaction (B20; respx, offline)."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx

from atlascore.connectors.nvd.cves import NvdCveConnector
from atlascore.connectors_sdk import SafeFetcher
from atlascore.connectors_sdk.base import FetchContext
from atlascore.core.clock import FixedClock
from atlascore.security.secrets import sanitize_for_storage

pytestmark = pytest.mark.unit

NOW = FixedClock(datetime(2026, 6, 1, tzinfo=UTC))
BASE = "https://services.nvd.nist.gov"
EMPTY = b'{"vulnerabilities": []}'


def _fetcher() -> SafeFetcher:
    return SafeFetcher(
        client=httpx.Client(),
        clock=NOW,
        user_agent="AtlasCore/test",
        resolver=lambda _host: ["93.184.216.34"],
        sleep=lambda _s: None,
        pin_connections=False,
    )


@respx.mock
def test_keyed_mode_sends_apikey_header() -> None:
    route = respx.get(url__startswith=BASE).mock(
        return_value=httpx.Response(
            200, content=EMPTY, headers={"content-type": "application/json"}
        )
    )
    ctx = FetchContext(feed_url=BASE, credential="my-nvd-key")
    NvdCveConnector().fetch(_fetcher(), ctx)
    assert route.calls.last.request.headers.get("apiKey") == "my-nvd-key"


@respx.mock
def test_keyless_mode_sends_no_apikey() -> None:
    route = respx.get(url__startswith=BASE).mock(
        return_value=httpx.Response(
            200, content=EMPTY, headers={"content-type": "application/json"}
        )
    )
    NvdCveConnector().fetch(_fetcher(), FetchContext(feed_url=BASE, credential=None))
    assert "apiKey" not in route.calls.last.request.headers


def test_apikey_redacted_by_sanitizer() -> None:
    # A stringified failing-request error containing the key must be redacted before storage (§14).
    leaked = "request failed with headers {'apiKey': 'my-nvd-key'} apiKey: my-nvd-key"
    safe = sanitize_for_storage(leaked)
    assert "my-nvd-key" not in safe
