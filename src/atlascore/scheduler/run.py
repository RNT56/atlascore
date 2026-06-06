"""The single-instance scheduler (B19): enqueue due fetches + run housekeeping inline.

Holds the scheduler advisory lock (single-scheduler invariant, §17) and, each tick:

1. **Enqueues per-source fetch jobs** that are *due* by their cron (``source.config['schedule']``),
   with a stable per-tick-bucket ``dedupe_key`` so overlapping ticks are idempotent. Only fetchable
   (approved + enabled) sources are considered (default-deny).
2. **Runs housekeeping inline** on independent cadence buckets — `freshness` (refresh source_health),
   `alert` (evaluate + deliver), `partition` (ensure yearly partitions), and `audit` rotation. These
   are system-wide control-plane tasks with no per-source row, and the scheduler is single-instance,
   so they run inline rather than as queued jobs (which would need a nullable ``crawl_job.source_id``
   migration; B16/B19 keep it migration-free).

The worker loop (``worker.loop``) drains the per-item fetch→parse→enrich pipeline. Each tick is a
short, broadly-guarded step so one bad tick never kills the daemon (CLAUDE.md §18). ``stop_event``
(set by SIGINT/SIGTERM) ends the loop cleanly and releases the lock.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from atlascore.alerts.delivery import DeliveryChannel
from atlascore.core.clock import Clock, SystemClock
from atlascore.core.heartbeat import ping_heartbeat
from atlascore.core.logging import get_logger
from atlascore.core.settings import Settings, get_settings
from atlascore.scheduler.cron import is_due, tick_bucket
from atlascore.scheduler.lock import scheduler_lock
from atlascore.schemas.crawl_job import JobType
from atlascore.schemas.source import PolicyStatus
from atlascore.storage.postgres.models import CrawlJob, Source
from atlascore.storage.queue import enqueue
from atlascore.storage.retention import run_retention
from atlascore.worker.freshness import refresh_source_health

_log = get_logger("atlascore.scheduler")


@dataclass(frozen=True)
class SchedulerTickResult:
    fetches_enqueued: int
    housekeeping_ran: list[str]


def _fetchable_sources(session: Session) -> list[Source]:
    rows = session.scalars(select(Source).where(Source.enabled.is_(True)))
    return [s for s in rows if PolicyStatus(s.policy_status).is_fetchable]


def _last_scheduled_fetch(session: Session, source_id: object) -> datetime | None:
    return session.scalar(
        select(func.max(CrawlJob.scheduled_at)).where(
            CrawlJob.source_id == source_id,
            CrawlJob.job_type == JobType.FETCH.value,
            CrawlJob.dedupe_key.like("fetch:%:sched:%"),
        )
    )


def enqueue_due_fetches(session: Session, *, now: datetime, tick: timedelta) -> int:
    """Enqueue one bucketed fetch job per source whose cron is due. Idempotent. Caller's txn."""
    enqueued = 0
    bucket = tick_bucket(now, tick).isoformat()
    for source in _fetchable_sources(session):
        schedule = (source.config or {}).get("schedule")
        if not schedule:
            continue  # no cron → not auto-scheduled (manual via `atlas crawl`)
        slug = (source.config or {}).get("slug") or str(source.id)
        if not is_due(schedule, last_scheduled=_last_scheduled_fetch(session, source.id), now=now):
            continue
        dedupe_key = f"fetch:{slug}:sched:{bucket}"
        existing = session.scalar(select(CrawlJob.id).where(CrawlJob.dedupe_key == dedupe_key))
        enqueue(
            session,
            source_id=source.id,
            job_type=JobType.FETCH,
            dedupe_key=dedupe_key,
            scheduled_at=now,
        )
        if existing is None:
            enqueued += 1
    return enqueued


class _Cadence:
    """Tracks the last cadence bucket a housekeeping task ran in (in-memory, single-scheduler)."""

    def __init__(self) -> None:
        self._last: dict[str, datetime] = {}

    def due(self, name: str, *, now: datetime, interval_seconds: int) -> bool:
        bucket = tick_bucket(now, timedelta(seconds=interval_seconds))
        if self._last.get(name) == bucket:
            return False
        self._last[name] = bucket
        return True


def run_housekeeping(
    engine: Engine,
    cadence: _Cadence,
    *,
    now: datetime,
    settings: Settings,
    channel: DeliveryChannel | None,
) -> list[str]:
    """Run any due inline housekeeping tasks (freshness/alert/partition/audit). Returns names run."""
    ran: list[str] = []
    if cadence.due("freshness", now=now, interval_seconds=settings.freshness_interval_seconds):
        with Session(engine) as session, session.begin():
            refresh_source_health(session, now=now)
        ran.append("freshness")
    if cadence.due("alert", now=now, interval_seconds=settings.alert_interval_seconds):
        from atlascore.worker.alerts import run_alerts  # local import avoids a cycle

        run_alerts(engine, channel=channel)
        ran.append("alert")
    if cadence.due("partition", now=now, interval_seconds=settings.partition_interval_seconds):
        from atlascore.worker.partitions import run_partition_maintenance

        run_partition_maintenance(engine)
        ran.append("partition")
    if cadence.due("audit", now=now, interval_seconds=settings.audit_rotation_interval_seconds):
        from atlascore.core.durations import parse_duration

        audit_w = parse_duration(settings.audit_routine_retention) or timedelta(days=180)
        crawl_w = parse_duration(settings.crawl_job_retention) or timedelta(days=90)
        with Session(engine) as session, session.begin():
            run_retention(session, now=now, audit_window=audit_w, crawl_window=crawl_w)
        ran.append("audit")
    return ran


def run_scheduler(
    engine: Engine,
    *,
    stop_event: threading.Event | None = None,
    clock: Clock | None = None,
    settings: Settings | None = None,
    channel: DeliveryChannel | None = None,
    max_ticks: int | None = None,
) -> None:
    """Acquire the scheduler lock and loop: enqueue due fetches + run housekeeping until stopped.

    ``max_ticks`` bounds the loop for tests; production passes ``None`` (run until ``stop_event``).
    """
    clock = clock or SystemClock()
    settings = settings or get_settings()
    stop = stop_event or threading.Event()
    cadence = _Cadence()
    tick = timedelta(seconds=settings.scheduler_tick_seconds)

    with scheduler_lock(engine):
        _log.info("scheduler_started", tick_seconds=settings.scheduler_tick_seconds)
        ticks = 0
        while not stop.is_set():
            now = clock.now()
            try:
                with Session(engine) as session, session.begin():
                    fetches = enqueue_due_fetches(session, now=now, tick=tick)
                ran = run_housekeeping(engine, cadence, now=now, settings=settings, channel=channel)
                if fetches or ran:
                    _log.info("scheduler_tick", fetches=fetches, housekeeping=ran)
                # Liveness beat AFTER a successful tick (best-effort; "disabled" when unconfigured):
                # a failing tick skips the ping, so a sustained outage stops the heartbeat and pages.
                ping_heartbeat(settings)
            except Exception as exc:  # one bad tick must not kill the daemon (§18)
                _log.warning("scheduler_tick_failed", error=str(exc))
            ticks += 1
            if max_ticks is not None and ticks >= max_ticks:
                break
            stop.wait(settings.scheduler_tick_seconds)
        _log.info("scheduler_stopped", ticks=ticks)


def run_one_scheduler_tick(
    engine: Engine,
    *,
    now: datetime,
    settings: Settings | None = None,
    channel: DeliveryChannel | None = None,
) -> SchedulerTickResult:
    """Run a single scheduler tick WITHOUT the lock/loop (for tests + the bounded smoke path)."""
    settings = settings or get_settings()
    tick = timedelta(seconds=settings.scheduler_tick_seconds)
    with Session(engine) as session, session.begin():
        fetches = enqueue_due_fetches(session, now=now, tick=tick)
    ran = run_housekeeping(engine, _Cadence(), now=now, settings=settings, channel=channel)
    return SchedulerTickResult(fetches_enqueued=fetches, housekeeping_ran=ran)
