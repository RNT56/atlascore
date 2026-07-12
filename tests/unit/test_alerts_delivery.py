"""Unit tests for alert delivery channels + the get_alerts tool contract (offline).

Channel selection, the LogChannel + webhook send (respx-mocked), severity ordering, and the
`AlertRow` → JSON mapper are verified without a DB. Rule evaluation (needs data) is integration.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest
import respx
from pydantic import SecretStr

from atlascore.alerts.delivery import (
    LogChannel,
    NtfyChannel,
    SmtpChannel,
    WebhookChannel,
    get_delivery_channel,
)
from atlascore.core.settings import Settings
from atlascore.mcp_server import ALERTS_LIMIT_MAX, TOOL_GET_ALERTS, build_mcp_server
from atlascore.mcp_server.server import _alert_to_payload
from atlascore.schemas.alert import SEVERITY_ORDER, AlertType, Severity
from atlascore.storage.alerts import AlertRow, EvidenceRef

pytestmark = pytest.mark.unit


def test_channel_selection_priority() -> None:
    assert isinstance(get_delivery_channel(Settings()), LogChannel)  # nothing configured
    assert isinstance(
        get_delivery_channel(Settings(alert_webhook_url=SecretStr("https://hook.test/x"))),
        WebhookChannel,
    )
    assert isinstance(get_delivery_channel(Settings(ntfy_topic="atlas")), NtfyChannel)
    assert isinstance(
        get_delivery_channel(Settings(smtp_host="smtp.test", smtp_from="a@test", smtp_to="b@test")),
        SmtpChannel,
    )


def test_empty_webhook_url_is_unconfigured() -> None:
    """An empty ALERT_WEBHOOK_URL (the blank value shipped in .env.example) must NOT select a
    WebhookChannel pointed at an empty URL — it falls through like the truthy ntfy/smtp checks."""
    assert isinstance(get_delivery_channel(Settings(alert_webhook_url=SecretStr(""))), LogChannel)
    # An empty webhook with ntfy configured must honor ntfy, not the empty webhook.
    assert isinstance(
        get_delivery_channel(Settings(alert_webhook_url=SecretStr(""), ntfy_topic="atlas")),
        NtfyChannel,
    )


def test_whitespace_only_values_are_unconfigured() -> None:
    """Whitespace-only config (webhook/ntfy/smtp) must NOT select a channel — it falls through."""
    assert isinstance(
        get_delivery_channel(Settings(alert_webhook_url=SecretStr("   "))), LogChannel
    )
    assert isinstance(get_delivery_channel(Settings(ntfy_topic="  ")), LogChannel)
    assert isinstance(
        get_delivery_channel(Settings(smtp_host="  ", smtp_from="  ", smtp_to="  ")), LogChannel
    )
    # Surrounding whitespace on a real webhook URL is stripped, not rejected.
    chan = get_delivery_channel(Settings(alert_webhook_url=SecretStr("  https://hook.test/x  ")))
    assert isinstance(chan, WebhookChannel)


def test_log_channel_send() -> None:
    assert LogChannel().send("subj", "body") == "logged"


@respx.mock
def test_webhook_channel_send() -> None:
    route = respx.post("https://hook.test/x").mock(return_value=httpx.Response(200))
    assert WebhookChannel("https://hook.test/x").send("subj", "body") == "sent"
    assert route.called
    assert route.calls.last.request.headers["content-type"].startswith("application/json")


@respx.mock
def test_webhook_channel_raises_on_error() -> None:
    respx.post("https://hook.test/x").mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        WebhookChannel("https://hook.test/x", attempts=1).send("s", "b")


@respx.mock
def test_webhook_retries_and_honors_retry_after() -> None:
    waits: list[float] = []
    route = respx.post("https://hook.test/x").mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after": "2"}),
            httpx.Response(200),
        ]
    )
    status = WebhookChannel("https://hook.test/x", sleep=waits.append).send("s", "b")
    assert status == "sent"
    assert waits == [2.0]
    assert route.call_count == 2


def test_severity_order() -> None:
    assert SEVERITY_ORDER[Severity.INFO] < SEVERITY_ORDER[Severity.CRITICAL]
    assert SEVERITY_ORDER[Severity.MEDIUM] < SEVERITY_ORDER[Severity.HIGH]


def test_get_alerts_registered_with_caps() -> None:
    server = build_mcp_server()
    tools = asyncio.run(server.list_tools())
    tool = next(t for t in tools if t.name == TOOL_GET_ALERTS)
    props = tool.inputSchema["properties"]
    assert props["limit"]["maximum"] == ALERTS_LIMIT_MAX
    assert {"alert_types", "status", "min_severity", "since"}.issubset(props.keys())
    assert tool.description is not None
    assert "read-only" in tool.description.lower()


def test_alert_payload_shape() -> None:
    row = AlertRow(
        alert_id=UUID(int=1),
        alert_type=AlertType.CVE_WATCH.value,
        rule_id="cve_watch_new",
        severity=Severity.MEDIUM.value,
        status="open",
        title="CVE-2026-1 — RCE",
        detail={"cvss": 9.8},
        evidence_doc_ids=[str(UUID(int=2))],
        created_at=datetime(2026, 6, 4, tzinfo=UTC),
        delivered_at=datetime(2026, 6, 4, 0, 1, tzinfo=UTC),
        delivery_status="sent",
        evidence=[EvidenceRef(UUID(int=2), "NVD", "https://nvd.test/CVE-2026-1")],
    )
    payload = _alert_to_payload(row)
    assert payload["alert_type"] == "cve_watch"
    assert payload["evidence_doc_ids"] == [str(UUID(int=2))]
    assert payload["provenance"]["evidence"][0]["source"] == "NVD"
    assert payload["delivery_status"] == "sent"
