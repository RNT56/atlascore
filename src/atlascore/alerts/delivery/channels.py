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
import time
from collections.abc import Callable
from email.message import EmailMessage
from typing import Protocol, runtime_checkable

import httpx

from atlascore.core.logging import get_logger
from atlascore.core.settings import Settings, get_settings

_log = get_logger("atlascore.alerts.delivery")
_TIMEOUT = 10.0
_ATTEMPTS = 3
_BACKOFF_SECONDS = 1.0


def _retry_after(exc: Exception) -> float | None:
    if not isinstance(exc, httpx.HTTPStatusError):
        return None
    value = exc.response.headers.get("retry-after", "")
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def _deliver_with_retry(
    operation: Callable[[], str],
    *,
    attempts: int,
    sleep: Callable[[float], None],
) -> str:
    """Retry transient delivery failures with bounded exponential/Retry-After backoff."""
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return operation()
        except Exception as exc:
            last_error = exc
            if attempt + 1 >= attempts:
                break
            delay = _retry_after(exc)
            sleep(delay if delay is not None else _BACKOFF_SECONDS * (2**attempt))
    assert last_error is not None  # noqa: S101 — attempts is always at least one
    raise last_error


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

    def __init__(
        self,
        url: str,
        *,
        attempts: int = _ATTEMPTS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._url = url
        self._attempts = max(1, attempts)
        self._sleep = sleep

    def send(self, subject: str, body: str) -> str:
        def operation() -> str:
            resp = httpx.post(self._url, json={"title": subject, "message": body}, timeout=_TIMEOUT)
            resp.raise_for_status()
            return "sent"

        return _deliver_with_retry(operation, attempts=self._attempts, sleep=self._sleep)


class NtfyChannel:
    """Publish to an ntfy topic (body as the message, subject as the ``Title`` header)."""

    name = "ntfy"

    def __init__(
        self,
        topic: str,
        *,
        base_url: str = "https://ntfy.sh",
        attempts: int = _ATTEMPTS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/{topic}"
        self._attempts = max(1, attempts)
        self._sleep = sleep

    def send(self, subject: str, body: str) -> str:
        def operation() -> str:
            resp = httpx.post(
                self._url,
                content=body.encode("utf-8"),
                headers={"Title": subject},
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            return "sent"

        return _deliver_with_retry(operation, attempts=self._attempts, sleep=self._sleep)


class SmtpChannel:
    """Send a plain-text email via SMTP (STARTTLS when a user/password is configured)."""

    name = "email"

    def __init__(
        self,
        settings: Settings,
        *,
        attempts: int = _ATTEMPTS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not settings.smtp_host or not settings.smtp_from or not settings.smtp_to:
            raise ValueError("SMTP channel needs SMTP_HOST, SMTP_FROM and SMTP_TO")
        self._settings = settings
        self._attempts = max(1, attempts)
        self._sleep = sleep

    def send(self, subject: str, body: str) -> str:
        s = self._settings
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = s.smtp_from or ""
        msg["To"] = s.smtp_to or ""
        msg.set_content(body)

        def operation() -> str:
            with smtplib.SMTP(s.smtp_host or "", s.smtp_port, timeout=_TIMEOUT) as smtp:
                if s.smtp_user and s.smtp_pass is not None:
                    smtp.starttls()
                    smtp.login(s.smtp_user, s.smtp_pass.get_secret_value())
                smtp.send_message(msg)
            return "sent"

        return _deliver_with_retry(operation, attempts=self._attempts, sleep=self._sleep)


def _clean(value: str | None) -> str:
    """Normalize a config value to a stripped string; '' means unconfigured.

    A blank ``ALERT_WEBHOOK_URL=`` (shipped in .env.example) loads as ``SecretStr('')`` and a
    whitespace-only value as ``'   '`` — neither should count as configured. Stripping here makes
    every channel's selection check (and the value handed to the channel) robust to both.
    """
    return value.strip() if value else ""


def get_delivery_channel(settings: Settings | None = None) -> DeliveryChannel:
    """Select the configured channel (webhook → ntfy → SMTP → log fallback).

    Blank/whitespace-only values are treated as unconfigured, so `cp .env.example .env` (which ships
    every channel blank) falls through to ``LogChannel`` instead of POSTing to an empty URL.
    """
    settings = settings or get_settings()
    webhook_url = _clean(
        settings.alert_webhook_url.get_secret_value()
        if settings.alert_webhook_url is not None
        else None
    )
    if webhook_url:
        return WebhookChannel(webhook_url)
    ntfy_topic = _clean(settings.ntfy_topic)
    if ntfy_topic:
        return NtfyChannel(ntfy_topic)
    if _clean(settings.smtp_host) and _clean(settings.smtp_to) and _clean(settings.smtp_from):
        return SmtpChannel(settings)
    return LogChannel()
