"""Cron-due math for the scheduler (B19), built on APScheduler's CronTrigger.

Each source carries a 5-field cron in ``source.config['schedule']`` (validated at registration). The
scheduler computes whether a source is **due** since its last scheduled fetch, and floors ``now`` to
a **tick bucket** so the per-tick ``dedupe_key`` is stable across overlapping ticks (idempotent
enqueue). All times are UTC.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from apscheduler.triggers.cron import CronTrigger

from atlascore.core.errors import ConfigError


def _trigger(cron_expr: str) -> CronTrigger:
    try:
        return CronTrigger.from_crontab(cron_expr, timezone=UTC)
    except (ValueError, TypeError) as exc:
        raise ConfigError(f"invalid cron schedule {cron_expr!r}: {exc}") from exc


def next_due_after(cron_expr: str, after: datetime) -> datetime | None:
    """The next fire time STRICTLY after ``after`` (UTC), or None if the cron never fires again.

    APScheduler's ``get_next_fire_time(previous_fire_time, now)`` returns the next fire ``>
    previous_fire_time`` and ``>= now`` — passing ``after`` for both yields strictly-after-``after``
    (so a source last scheduled exactly on a fire boundary is not considered due again at that instant).
    """
    fire = _trigger(cron_expr).get_next_fire_time(after, after)
    return fire.astimezone(UTC) if fire is not None else None


def is_due(cron_expr: str, *, last_scheduled: datetime | None, now: datetime) -> bool:
    """True if the cron should have fired since ``last_scheduled`` (or ever, if never scheduled)."""
    if last_scheduled is None:
        return True  # first-ever run: enqueue immediately
    nxt = next_due_after(cron_expr, last_scheduled)
    return nxt is not None and nxt <= now


def tick_bucket(now: datetime, interval: timedelta) -> datetime:
    """Floor ``now`` to a multiple of ``interval`` from the epoch (stable per-tick dedupe key)."""
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    elapsed = (now - epoch) // interval
    return epoch + elapsed * interval
