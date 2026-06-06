"""Unit: the optional liveness heartbeat / dead-man's-switch (respx, no live network)."""

from __future__ import annotations

import httpx
import pytest
import respx

from atlascore.core.heartbeat import ping_heartbeat
from atlascore.core.settings import Settings

pytestmark = pytest.mark.unit

_URL = "https://hc.example/ping/abc123"


def test_disabled_when_no_url() -> None:
    assert ping_heartbeat(Settings(heartbeat_url=None)) == "disabled"


def test_disabled_when_blank_or_whitespace_url() -> None:
    assert ping_heartbeat(Settings(heartbeat_url="")) == "disabled"
    assert ping_heartbeat(Settings(heartbeat_url="   ")) == "disabled"


@respx.mock
def test_pings_configured_url() -> None:
    route = respx.get(_URL).mock(return_value=httpx.Response(200))
    assert ping_heartbeat(Settings(heartbeat_url=_URL)) == "pinged"
    assert route.called


@respx.mock
def test_http_error_is_swallowed() -> None:
    respx.get(_URL).mock(return_value=httpx.Response(500))
    assert ping_heartbeat(Settings(heartbeat_url=_URL)) == "failed"


@respx.mock
def test_connect_error_is_swallowed() -> None:
    respx.get(_URL).mock(side_effect=httpx.ConnectError("boom"))
    assert ping_heartbeat(Settings(heartbeat_url=_URL)) == "failed"
