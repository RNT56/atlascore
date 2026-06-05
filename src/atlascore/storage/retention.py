"""Control-plane retention: rotate routine ``audit_log`` + prune terminal ``crawl_job`` rows.

Pure DML, no schema change (the R14 columns exist since migrations 0002/0004). Two invariants:

- **R14 (audit):** delete ONLY ``retention_class='routine'`` rows older than the window. ``purge``
  rows (erasure proof) are **structurally untouchable** — the filter is on ``retention_class``,
  never on action strings.
- **crawl_job:** prune ONLY terminal (``done``/``dead``) rows older than the window (keyed on
  ``coalesce(finished_at, created_at)``). ``pending``/``claimed``/``running``/``failed`` are never
  pruned, regardless of age.

``run_retention`` orchestrates both in the caller's transaction and writes ONE **retained**
(``retention_class='purge'``) bookkeeping entry recording counts only — never row content — mirroring
the ``atlas purge`` retained-audit pattern. The CLI (``atlas audit rotate``) and the scheduler both
call ``run_retention``; ``now`` is injected for deterministic tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, Delete, delete, func, select
from sqlalchemy.orm import Session

from atlascore.schemas.audit_log import Actor, RetentionClass
from atlascore.schemas.crawl_job import JobStatus
from atlascore.storage.audit import write_audit_log
from atlascore.storage.postgres.models import AuditLog, CrawlJob

AUDIT_ROTATE_ACTION = "audit_rotate"
_TERMINAL_STATES = [JobStatus.DONE.value, JobStatus.DEAD.value]


def _rowcount(session: Session, stmt: Delete) -> int:
    return cast("CursorResult[Any]", session.execute(stmt)).rowcount


@dataclass(frozen=True)
class AuditRotationResult:
    window_seconds: int
    cutoff: datetime
    deleted: int
    purge_retained_under_cutoff: int  # purge rows older than the cutoff that were KEPT (R14 proof)


@dataclass(frozen=True)
class CrawlJobPruneResult:
    window_seconds: int
    cutoff: datetime
    deleted_done: int
    deleted_dead: int


@dataclass(frozen=True)
class RetentionRunResult:
    audit: AuditRotationResult
    crawl: CrawlJobPruneResult
    dry_run: bool


def rotate_audit_log(
    session: Session, *, now: datetime, window: timedelta, dry_run: bool = False
) -> AuditRotationResult:
    """Delete routine audit rows older than ``now - window``; purge rows are never touched."""
    cutoff = now - window
    routine = AuditLog.retention_class == RetentionClass.ROUTINE.value
    older = AuditLog.ts < cutoff
    # R14 proof metric: how many purge rows are older than the cutoff yet retained.
    purge_retained = (
        session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.retention_class == RetentionClass.PURGE.value, older)
        )
        or 0
    )
    if dry_run:
        deleted = (
            session.scalar(select(func.count()).select_from(AuditLog).where(routine, older)) or 0
        )
    else:
        deleted = _rowcount(session, delete(AuditLog).where(routine, older))
    return AuditRotationResult(
        window_seconds=int(window.total_seconds()),
        cutoff=cutoff,
        deleted=deleted,
        purge_retained_under_cutoff=purge_retained,
    )


def prune_crawl_jobs(
    session: Session, *, now: datetime, window: timedelta, dry_run: bool = False
) -> CrawlJobPruneResult:
    """Prune terminal (done|dead) crawl_job rows older than ``now - window`` (else are kept)."""
    cutoff = now - window
    old_terminal = func.coalesce(CrawlJob.finished_at, CrawlJob.created_at) < cutoff

    def _count(status: str) -> int:
        return (
            session.scalar(
                select(func.count())
                .select_from(CrawlJob)
                .where(CrawlJob.status == status, old_terminal)
            )
            or 0
        )

    deleted_done = _count(JobStatus.DONE.value)
    deleted_dead = _count(JobStatus.DEAD.value)
    if not dry_run:
        _rowcount(
            session,
            delete(CrawlJob).where(CrawlJob.status.in_(_TERMINAL_STATES), old_terminal),
        )
    return CrawlJobPruneResult(
        window_seconds=int(window.total_seconds()),
        cutoff=cutoff,
        deleted_done=deleted_done,
        deleted_dead=deleted_dead,
    )


def run_retention(
    session: Session,
    *,
    now: datetime,
    audit_window: timedelta,
    crawl_window: timedelta,
    dry_run: bool = False,
) -> RetentionRunResult:
    """Rotate audit + prune crawl_job in one txn; write a retained bookkeeping entry (non-dry-run)."""
    audit = rotate_audit_log(session, now=now, window=audit_window, dry_run=dry_run)
    crawl = prune_crawl_jobs(session, now=now, window=crawl_window, dry_run=dry_run)
    if not dry_run:
        # Retained (purge-class) proof-of-rotation — counts only, never personal data.
        write_audit_log(
            session,
            action=AUDIT_ROTATE_ACTION,
            actor=Actor.CLI,
            retention_class=RetentionClass.PURGE,
            detail={
                "audit_deleted": audit.deleted,
                "audit_cutoff": audit.cutoff.isoformat(),
                "audit_window_seconds": audit.window_seconds,
                "crawl_deleted_done": crawl.deleted_done,
                "crawl_deleted_dead": crawl.deleted_dead,
                "crawl_cutoff": crawl.cutoff.isoformat(),
                "crawl_window_seconds": crawl.window_seconds,
            },
        )
    return RetentionRunResult(audit=audit, crawl=crawl, dry_run=dry_run)
