"""Optional liveness heartbeat / dead-man's-switch (operational, CLAUDE.md §17).

The single-instance scheduler pings an external uptime monitor (e.g. healthchecks.io) after each
successful tick. When the box, Postgres, or the scheduler process dies the pings stop and the
monitor PAGES the operator — so a total outage becomes an active alert instead of a silence the
operator must happen to notice. This is the one structural piece that makes "unattended for a
month" safe: the daily digest reports *partial* failures, but only an external heartbeat catches a
whole-box / dead-DB / dead-scheduler outage.

Best-effort by design: a failed ping is logged (WITHOUT the URL — it carries a per-check token, and
httpx exception messages embed the URL, so only the exception *type* is logged) and never raises.
Monitoring must never break the loop it monitors.
"""

from __future__ import annotations

import httpx

from atlascore.core.logging import get_logger
from atlascore.core.settings import Settings, get_settings

_log = get_logger("atlascore.heartbeat")


def ping_heartbeat(settings: Settings | None = None, *, client: httpx.Client | None = None) -> str:
    """GET the configured heartbeat URL. Returns ``pinged`` | ``disabled`` | ``failed``.

    The URL is operator-configured trusted config (like ``DATABASE_URL``), not agent-supplied, so
    the SSRF guard (§14, for fetched/agent URLs) does not apply. Never raises; never logs the URL.
    """
    settings = settings or get_settings()
    secret = settings.heartbeat_url
    url = secret.get_secret_value().strip() if secret is not None else ""
    if not url:
        return "disabled"
    timeout = settings.heartbeat_timeout
    try:
        if client is not None:
            client.get(url, timeout=timeout).raise_for_status()
        else:
            httpx.get(url, timeout=timeout).raise_for_status()
        return "pinged"
    except Exception as exc:  # monitoring must never break the loop it monitors (and no URL leak)
        _log.warning("heartbeat_ping_failed", error=type(exc).__name__)
        return "failed"


__all__ = ["ping_heartbeat"]
