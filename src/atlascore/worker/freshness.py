"""The freshness/health job — computes ``source_health`` (docs/schemas §2.12, runbooks §8).

`freshness_lag = now - coalesce(last_item_published_at, last_success_at)`, classified against the
source's `freshness` thresholds. This is the `job_type=freshness` work: a pure, read-only
computation over `crawl_job` + `document` followed by an idempotent upsert of the current-snapshot
`source_health` row. It is **control-plane** (no MCP dependency) and is driven by `atlas health` /
`atlas digest` (and, once the scheduler lands, by a scheduled `freshness` job calling the same
`refresh_source_health`).

Status precedence (docs/runbooks §8): `cooling_down` (≥ `COOLING_THRESHOLD` consecutive failed
fetches) > `stale` (lag past `error_after`) > `warn` (lag past `warn_after`) > `error` (has failed
and never produced data) > `ok`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from atlascore.core.durations import parse_duration
from atlascore.schemas.crawl_job import JobStatus, JobType
from atlascore.schemas.source_health import HealthStatus
from atlascore.storage.postgres.models import CrawlJob, Document, Source, SourceHealth

# Consecutive failed fetches that trip the circuit breaker (-> cooling_down).
COOLING_THRESHOLD = 3
# Rolling window for the rate/quality metrics.
_WINDOW = timedelta(hours=24)
# How far back to scan fetch outcomes when counting the trailing failure streak.
_STREAK_SCAN = 50


@dataclass(frozen=True)
class SourceHealthSnapshot:
    """The computed health of one source (before it is upserted into ``source_health``)."""

    source_id: UUID
    last_attempt_at: datetime | None
    last_success_at: datetime | None
    last_item_published_at: datetime | None
    freshness_lag: timedelta | None
    status: HealthStatus
    consecutive_failures: int
    success_rate_24h: float | None
    parse_quality: float | None
    duplicate_rate: float | None
    item_count_recent: int


def classify_status(
    freshness_lag: timedelta | None,
    consecutive_failures: int,
    *,
    warn_after: timedelta | None,
    error_after: timedelta | None,
    cooling_threshold: int = COOLING_THRESHOLD,
) -> HealthStatus:
    """Pure status classification (docs/runbooks §8). Unit-tested in isolation."""
    if consecutive_failures >= cooling_threshold:
        return HealthStatus.COOLING_DOWN
    if freshness_lag is not None:
        if error_after is not None and freshness_lag > error_after:
            return HealthStatus.STALE
        if warn_after is not None and freshness_lag > warn_after:
            return HealthStatus.WARN
    if consecutive_failures > 0 and freshness_lag is None:
        return HealthStatus.ERROR
    return HealthStatus.OK


def _threshold(freshness: dict[str, object], key: str) -> timedelta | None:
    """Parse one freshness threshold (``warn_after``/``error_after``) to a timedelta, or None."""
    value = freshness.get(key)
    if not isinstance(value, str):
        return None
    try:
        return parse_duration(value, allow_permanent=False, allow_hours=True)
    except ValueError:
        return None


def _outcome_ratio(
    session: Session,
    source_id: UUID,
    job_type: JobType,
    since: datetime,
    *,
    success: set[str],
    failure: set[str],
) -> float | None:
    """success / (success + failure) over jobs of one type finished within the window; None if 0."""
    rows = session.execute(
        select(CrawlJob.status, func.count())
        .where(
            CrawlJob.source_id == source_id,
            CrawlJob.job_type == job_type.value,
            CrawlJob.finished_at >= since,
            CrawlJob.status.in_(success | failure),
        )
        .group_by(CrawlJob.status)
    ).all()
    counts: dict[str, int] = {row[0]: row[1] for row in rows}
    good = sum(counts.get(s, 0) for s in success)
    bad = sum(counts.get(f, 0) for f in failure)
    total = good + bad
    return (good / total) if total else None


def _consecutive_failures(session: Session, source_id: UUID) -> int:
    """Count trailing failed/dead fetch jobs since the last successful fetch."""
    statuses = list(
        session.scalars(
            select(CrawlJob.status)
            .where(
                CrawlJob.source_id == source_id,
                CrawlJob.job_type == JobType.FETCH.value,
                CrawlJob.status.in_([JobStatus.DONE.value, JobStatus.DEAD.value]),
                CrawlJob.finished_at.is_not(None),
            )
            .order_by(CrawlJob.finished_at.desc())
            .limit(_STREAK_SCAN)
        )
    )
    streak = 0
    for status in statuses:
        if status == JobStatus.DONE.value:
            break
        streak += 1
    return streak


def compute_source_health(
    session: Session, source: Source, *, now: datetime, cooling_threshold: int = COOLING_THRESHOLD
) -> SourceHealthSnapshot:
    """Compute (but do not persist) the health snapshot for one source (read-only)."""
    sid = source.id
    is_fetch = CrawlJob.job_type == JobType.FETCH.value

    last_attempt_at = session.scalar(
        select(func.max(CrawlJob.claimed_at)).where(CrawlJob.source_id == sid, is_fetch)
    )
    last_success_at = session.scalar(
        select(func.max(CrawlJob.finished_at)).where(
            CrawlJob.source_id == sid, is_fetch, CrawlJob.status == JobStatus.DONE.value
        )
    )
    last_item_published_at = session.scalar(
        select(func.max(Document.published_at)).where(Document.source_id == sid)
    )
    consecutive_failures = _consecutive_failures(session, sid)

    since = now - _WINDOW
    success_rate_24h = _outcome_ratio(
        session,
        sid,
        JobType.FETCH,
        since,
        success={JobStatus.DONE.value},
        failure={JobStatus.FAILED.value, JobStatus.DEAD.value},
    )
    parse_quality = _outcome_ratio(
        session,
        sid,
        JobType.PARSE,
        since,
        success={JobStatus.DONE.value},
        failure={JobStatus.DEAD.value},
    )
    item_count_recent = (
        session.scalar(
            select(func.count())
            .select_from(Document)
            .where(Document.source_id == sid, Document.ingested_at >= since)
        )
        or 0
    )

    basis = last_item_published_at or last_success_at
    freshness_lag = (now - basis) if basis is not None else None
    freshness = source.freshness or {}
    status = classify_status(
        freshness_lag,
        consecutive_failures,
        warn_after=_threshold(freshness, "warn_after"),
        error_after=_threshold(freshness, "error_after"),
        cooling_threshold=cooling_threshold,
    )
    return SourceHealthSnapshot(
        source_id=sid,
        last_attempt_at=last_attempt_at,
        last_success_at=last_success_at,
        last_item_published_at=last_item_published_at,
        freshness_lag=freshness_lag,
        status=status,
        consecutive_failures=consecutive_failures,
        success_rate_24h=success_rate_24h,
        parse_quality=parse_quality,
        duplicate_rate=None,  # MVP: per-item dedupe is not yet tracked (documented follow-up).
        item_count_recent=item_count_recent,
    )


def refresh_source_health(
    session: Session, *, now: datetime, source_id: UUID | None = None
) -> list[SourceHealth]:
    """Compute + idempotently upsert ``source_health`` for one source (or all). Caller owns the txn."""
    if source_id is not None:
        one = session.get(Source, source_id)
        sources = [one] if one is not None else []
    else:
        sources = list(session.scalars(select(Source).order_by(Source.name)))

    rows: list[SourceHealth] = []
    for source in sources:
        snap = compute_source_health(session, source, now=now)
        row = session.get(SourceHealth, source.id)
        if row is None:
            row = SourceHealth(source_id=source.id)
            session.add(row)
        row.last_attempt_at = snap.last_attempt_at
        row.last_success_at = snap.last_success_at
        row.last_item_published_at = snap.last_item_published_at
        row.freshness_lag = snap.freshness_lag
        row.status = snap.status.value
        row.consecutive_failures = snap.consecutive_failures
        row.success_rate_24h = snap.success_rate_24h
        row.parse_quality = snap.parse_quality
        row.duplicate_rate = snap.duplicate_rate
        row.item_count_recent = snap.item_count_recent
        row.updated_at = now
        rows.append(row)
    session.flush()
    return rows
