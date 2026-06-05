"""Integration: B16 retention rotation/prune against Postgres 16.

Proves the R14 invariant (purge audit rows are NEVER rotated, even when ancient), routine rows beyond
the window are deleted while recent ones survive, terminal crawl_job rows are pruned (pending kept),
the single retained bookkeeping entry, dry-run safety, and idempotency.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session

from atlascore.schemas.audit_log import Actor, RetentionClass
from atlascore.schemas.crawl_job import JobStatus, JobType
from atlascore.storage.postgres.models import AuditLog, CrawlJob, Source
from atlascore.storage.retention import prune_crawl_jobs, rotate_audit_log, run_retention

pytestmark = pytest.mark.integration

NOW = datetime(2026, 6, 6, 12, 0, tzinfo=UTC)
AUDIT_WINDOW = timedelta(days=180)
CRAWL_WINDOW = timedelta(days=90)
TAG = "ret_it"  # marks this test's audit rows for cleanup


def _audit(session: Session, *, retention_class: RetentionClass, age_days: int, n: int = 1) -> None:
    for _ in range(n):
        session.add(
            AuditLog(
                ts=NOW - timedelta(days=age_days),
                action=f"{TAG}_{retention_class.value}",
                actor=Actor.SYSTEM.value,
                retention_class=retention_class.value,
                detail={},
            )
        )


def _cleanup(session: Session) -> None:
    session.execute(delete(AuditLog).where(AuditLog.action.like(f"{TAG}%")))
    session.execute(delete(AuditLog).where(AuditLog.action == "audit_rotate"))
    src_ids = list(session.scalars(select(Source.id).where(Source.name == "retention-it-source")))
    if src_ids:
        session.execute(delete(CrawlJob).where(CrawlJob.source_id.in_(src_ids)))
        session.execute(delete(Source).where(Source.id.in_(src_ids)))


def test_routine_rotated_purge_retained_even_when_ancient(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)
        _audit(session, retention_class=RetentionClass.ROUTINE, age_days=200, n=3)  # old → delete
        _audit(session, retention_class=RetentionClass.ROUTINE, age_days=10, n=2)  # recent → keep
        _audit(session, retention_class=RetentionClass.PURGE, age_days=9999, n=2)  # ancient → KEEP

    with Session(migrated_engine) as session, session.begin():
        result = rotate_audit_log(session, now=NOW, window=AUDIT_WINDOW)
    assert result.deleted == 3
    assert result.purge_retained_under_cutoff == 2  # R14 proof metric

    with Session(migrated_engine) as session:
        routine_left = session.scalar(
            select(func.count()).select_from(AuditLog).where(AuditLog.action == f"{TAG}_routine")
        )
        purge_left = session.scalar(
            select(func.count()).select_from(AuditLog).where(AuditLog.action == f"{TAG}_purge")
        )
        assert routine_left == 2  # only the recent routine rows remain
        assert purge_left == 2  # the ancient purge rows survived — R14

    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)


def _seed_jobs(session: Session) -> None:
    source = Source(
        name="retention-it-source",
        category="news_rss",
        access_method="rss",
        policy_status="approved",
        enabled=True,
        config={},
        schema_version=1,
    )
    session.add(source)
    session.flush()
    # (status, finished_at age, created_at age)
    rows = [
        (JobStatus.DONE, 100, 100),  # old done → prune
        (JobStatus.DEAD, 120, 120),  # old dead → prune
        (JobStatus.DONE, 5, 5),  # recent done → keep
        (JobStatus.PENDING, None, 365),  # ancient pending → KEEP (not terminal)
        (JobStatus.CLAIMED, None, 365),  # ancient claimed → KEEP
        (JobStatus.FAILED, None, 365),  # ancient failed → KEEP (not terminal)
    ]
    for i, (status, fin_age, made_age) in enumerate(rows):
        session.add(
            CrawlJob(
                source_id=source.id,
                job_type=JobType.FETCH.value,
                status=status.value,
                dedupe_key=f"{TAG}:job:{i}",
                scheduled_at=NOW - timedelta(days=made_age),
                created_at=NOW - timedelta(days=made_age),
                finished_at=None if fin_age is None else NOW - timedelta(days=fin_age),
            )
        )


def test_crawl_prune_terminal_only(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)
        _seed_jobs(session)

    with Session(migrated_engine) as session, session.begin():
        result = prune_crawl_jobs(session, now=NOW, window=CRAWL_WINDOW)
    assert result.deleted_done == 1
    assert result.deleted_dead == 1

    with Session(migrated_engine) as session:
        remaining = {
            r.dedupe_key: r.status
            for r in session.scalars(
                select(CrawlJob).where(CrawlJob.dedupe_key.like(f"{TAG}:job:%"))
            )
        }
        # The two old terminal jobs are gone; recent done + all non-terminal survive.
        assert len(remaining) == 4
        assert all(s in {"done", "pending", "claimed", "failed"} for s in remaining.values())

    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)


def test_run_retention_bookkeeping_dry_run_and_idempotent(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)
        _audit(session, retention_class=RetentionClass.ROUTINE, age_days=200, n=2)
        _seed_jobs(session)

    # Dry-run: deletes nothing, writes no bookkeeping entry.
    with Session(migrated_engine) as session, session.begin():
        dry = run_retention(
            session, now=NOW, audit_window=AUDIT_WINDOW, crawl_window=CRAWL_WINDOW, dry_run=True
        )
    assert dry.audit.deleted == 2 and dry.crawl.deleted_done == 1  # reported but not applied
    with Session(migrated_engine) as session:
        assert (
            session.scalar(
                select(func.count()).select_from(AuditLog).where(AuditLog.action == "audit_rotate")
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == f"{TAG}_routine")
            )
            == 2  # nothing deleted by dry-run
        )

    # Real run: deletes + writes exactly one retained (purge-class) bookkeeping entry.
    with Session(migrated_engine) as session, session.begin():
        run_retention(session, now=NOW, audit_window=AUDIT_WINDOW, crawl_window=CRAWL_WINDOW)
    with Session(migrated_engine) as session:
        bk = session.scalars(select(AuditLog).where(AuditLog.action == "audit_rotate")).all()
        assert len(bk) == 1
        assert bk[0].retention_class == RetentionClass.PURGE.value
        assert bk[0].detail["audit_deleted"] == 2
        assert "personal" not in str(bk[0].detail)

    # Idempotent: a second run deletes 0 routine rows (only the retained bookkeeping entries remain).
    with Session(migrated_engine) as session, session.begin():
        again = run_retention(
            session, now=NOW, audit_window=AUDIT_WINDOW, crawl_window=CRAWL_WINDOW
        )
    assert again.audit.deleted == 0 and again.crawl.deleted_done == 0

    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)
