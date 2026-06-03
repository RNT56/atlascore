"""The Postgres-backed job queue (docs/schemas §2.2, CLAUDE.md §17).

Workers claim with ``SELECT … FOR UPDATE SKIP LOCKED``; jobs over ``max_attempts`` go to the
``dead`` DLQ; stale claims (older than the visibility timeout) are reclaimed to ``pending``.
All functions take an injected ``now`` (UTC) so retry timing is deterministic in tests, and
operate on a caller-managed transaction (the claim must run inside one for row locking).
``last_error``/``payload`` are routed through ``sanitize_for_storage`` before write (§14).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import CursorResult, select, update
from sqlalchemy.orm import Session

from atlascore.schemas.crawl_job import JobStatus, JobType
from atlascore.security.secrets import sanitize_for_storage
from atlascore.storage.postgres.models import CrawlJob

DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_VISIBILITY_TIMEOUT = timedelta(minutes=15)
_BACKOFF_BASE = timedelta(seconds=30)
_BACKOFF_CAP = timedelta(hours=1)


def retry_delay(attempt: int) -> timedelta:
    """Exponential backoff for the next retry, capped. (Jitter is added with the B4 fetcher.)"""
    if attempt < 1:
        attempt = 1
    factor = 2 ** (attempt - 1)
    seconds = min(_BACKOFF_BASE.total_seconds() * factor, _BACKOFF_CAP.total_seconds())
    return timedelta(seconds=seconds)


def enqueue(
    session: Session,
    *,
    source_id: UUID,
    job_type: JobType,
    dedupe_key: str,
    scheduled_at: datetime,
    payload: dict[str, Any] | None = None,
    priority: int = 0,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> CrawlJob:
    """Insert a job, idempotent on ``dedupe_key`` (a replay returns the existing job)."""
    existing = session.scalar(select(CrawlJob).where(CrawlJob.dedupe_key == dedupe_key))
    if existing is not None:
        return existing
    job = CrawlJob(
        source_id=source_id,
        job_type=job_type.value,
        dedupe_key=dedupe_key,
        scheduled_at=scheduled_at,
        payload=sanitize_for_storage(payload) if payload is not None else None,
        priority=priority,
        max_attempts=max_attempts,
    )
    session.add(job)
    session.flush()
    return job


def claim_next(
    session: Session,
    *,
    worker_id: str,
    now: datetime,
    job_types: Sequence[JobType] | None = None,
) -> CrawlJob | None:
    """Atomically claim the next due ``pending`` job via ``FOR UPDATE SKIP LOCKED``.

    Must run inside a transaction. Marks the job ``claimed`` and increments ``attempt``.
    """
    stmt = select(CrawlJob).where(
        CrawlJob.status == JobStatus.PENDING.value,
        CrawlJob.scheduled_at <= now,
    )
    if job_types:
        stmt = stmt.where(CrawlJob.job_type.in_([t.value for t in job_types]))
    stmt = (
        stmt.order_by(CrawlJob.priority.desc(), CrawlJob.scheduled_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job = session.scalar(stmt)
    if job is None:
        return None
    job.status = JobStatus.CLAIMED.value
    job.claimed_at = now
    job.claimed_by = worker_id
    job.attempt += 1
    session.flush()
    return job


def mark_running(session: Session, job: CrawlJob) -> None:
    job.status = JobStatus.RUNNING.value
    session.flush()


def complete(session: Session, job: CrawlJob, *, now: datetime) -> None:
    job.status = JobStatus.DONE.value
    job.finished_at = now
    job.last_error = None
    session.flush()


def fail(
    session: Session,
    job: CrawlJob,
    *,
    now: datetime,
    error: str | None = None,
) -> None:
    """Record a failure: retry (``pending`` with backoff) until ``max_attempts``, then ``dead``."""
    job.last_error = sanitize_for_storage(error) if error is not None else None
    if job.attempt >= job.max_attempts:
        job.status = JobStatus.DEAD.value
        job.finished_at = now
    else:
        job.status = JobStatus.PENDING.value
        job.scheduled_at = now + retry_delay(job.attempt)
        job.claimed_at = None
        job.claimed_by = None
    session.flush()


def reclaim_stale(
    session: Session,
    *,
    now: datetime,
    visibility_timeout: timedelta = DEFAULT_VISIBILITY_TIMEOUT,
) -> int:
    """Return claimed/running jobs whose claim is older than the visibility timeout to
    ``pending`` (crash safety). Returns the number reclaimed."""
    cutoff = now - visibility_timeout
    stmt = (
        update(CrawlJob)
        .where(
            CrawlJob.status.in_([JobStatus.CLAIMED.value, JobStatus.RUNNING.value]),
            CrawlJob.claimed_at < cutoff,
        )
        .values(status=JobStatus.PENDING.value, claimed_at=None, claimed_by=None)
        .execution_options(synchronize_session=False)
    )
    result = cast("CursorResult[Any]", session.execute(stmt))
    return result.rowcount


def dead_jobs(session: Session, *, limit: int = 100) -> list[CrawlJob]:
    """Return jobs in the DLQ (``status=dead``), oldest first."""
    stmt = (
        select(CrawlJob)
        .where(CrawlJob.status == JobStatus.DEAD.value)
        .order_by(CrawlJob.created_at)
        .limit(limit)
    )
    return list(session.scalars(stmt))
