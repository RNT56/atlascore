"""Alert/digest delivery channels (docs/mcp §3.4, CLAUDE.md §11.13).

One configured outbound channel, selected from settings: ``ALERT_WEBHOOK_URL`` → webhook,
else ``NTFY_TOPIC`` → ntfy, else SMTP (``SMTP_HOST`` + ``SMTP_TO``), else a stdout/log fallback.
A channel turns a ``(subject, body)`` into a delivery and returns a short ``delivery_status``
string; failures raise (the caller records ``failed: …``).

These endpoints are **operator-configured trusted config** (like ``DATABASE_URL``), not
agent-supplied, so the SSRF guard (§14, for fetched/agent URLs) does not apply here. This channel
also backs the B8 daily digest (`atlas digest`), replacing its stdout-only seam.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from typing import Protocol, runtime_checkable

import httpx

from atlascore.core.logging import get_logger
from atlascore.core.settings import Settings, get_settings

_log = get_logger("atlascore.alerts.delivery")
_TIMEOUT = 10.0


@runtime_checkable
class DeliveryChannel(Protocol):
    @property
    def name(self) -> str: ...

    def send(self, subject: str, body: str) -> str:
        """Deliver one message; return a short status (e.g. ``sent``/``logged``). Raises on failure."""
        ...


class LogChannel:
    """Fallback: structured log + stdout. Always available; never raises."""

    name = "log"

    def send(self, subject: str, body: str) -> str:
        _log.info("alert_delivery", channel=self.name, subject=subject)
        return "logged"


class WebhookChannel:
    """POST a JSON ``{title, message}`` to a generic webhook (Slack/Discord-style)."""

    name = "webhook"

    def __init__(self, url: str) -> None:
        self._url = url

    def send(self, subject: str, body: str) -> str:
        resp = httpx.post(self._url, json={"title": subject, "message": body}, timeout=_TIMEOUT)
        resp.raise_for_status()
        return "sent"


class NtfyChannel:
    """Publish to an ntfy topic (body as the message, subject as the ``Title`` header)."""

    name = "ntfy"

    def __init__(self, topic: str, *, base_url: str = "https://ntfy.sh") -> None:
        self._url = f"{base_url.rstrip('/')}/{topic}"

    def send(self, subject: str, body: str) -> str:
        resp = httpx.post(
            self._url, content=body.encode("utf-8"), headers={"Title": subject}, timeout=_TIMEOUT
        )
        resp.raise_for_status()
        return "sent"


class SmtpChannel:
    """Send a plain-text email via SMTP (STARTTLS when a user/password is configured)."""

    name = "email"

    def __init__(self, settings: Settings) -> None:
        if not settings.smtp_host or not settings.smtp_from or not settings.smtp_to:
            raise ValueError("SMTP channel needs SMTP_HOST, SMTP_FROM and SMTP_TO")
        self._settings = settings

    def send(self, subject: str, body: str) -> str:
        s = self._settings
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = s.smtp_from or ""
        msg["To"] = s.smtp_to or ""
        msg.set_content(body)
        with smtplib.SMTP(s.smtp_host or "", s.smtp_port, timeout=_TIMEOUT) as smtp:
            if s.smtp_user and s.smtp_pass is not None:
                smtp.starttls()
                smtp.login(s.smtp_user, s.smtp_pass.get_secret_value())
            smtp.send_message(msg)
        return "sent"


def get_delivery_channel(settings: Settings | None = None) -> DeliveryChannel:
    """Select the configured channel (webhook → ntfy → SMTP → log fallback)."""
    settings = settings or get_settings()
    if settings.alert_webhook_url is not None:
        return WebhookChannel(settings.alert_webhook_url.get_secret_value())
    if settings.ntfy_topic:
        return NtfyChannel(settings.ntfy_topic)
    if settings.smtp_host and settings.smtp_to and settings.smtp_from:
        return SmtpChannel(settings)
    return LogChannel()
