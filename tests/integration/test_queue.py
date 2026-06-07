"""Integration: the crawl_job queue state machine against a real Postgres 16 (B3 done-when).

Covers enqueue idempotency, claim/complete, fail→retry→dead, stale-claim reclaim, and
`FOR UPDATE SKIP LOCKED` across two workers. Rollback-isolated except the SKIP-LOCKED test,
which commits (so a second connection can see the rows) and cleans up after itself.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, delete
from sqlalchemy.orm import Session

from atlascore.schemas.crawl_job import JobStatus, JobType
from atlascore.storage.postgres.models import CrawlJob, Source
from atlascore.storage.queue import (
    claim_next,
    complete,
    enqueue,
    fail,
    reclaim_stale,
    requeue_dead,
)
from atlascore.storage.sources import load_source_yaml, upsert_source

pytestmark = pytest.mark.integration

FIX = Path("tests/fixtures/sources")
NOW = datetime(2026, 6, 3, 12, 0, 0, tzinfo=UTC)


def _source(session: Session) -> Source:
    return upsert_source(session, load_source_yaml(FIX / "approved_rss.yaml").to_record())


def test_enqueue_is_idempotent(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        src = _source(session)
        session.flush()
        first = enqueue(
            session, source_id=src.id, job_type=JobType.FETCH, dedupe_key="dk", scheduled_at=NOW
        )
        session.flush()
        again = enqueue(
            session, source_id=src.id, job_type=JobType.FETCH, dedupe_key="dk", scheduled_at=NOW
        )
        assert again.id == first.id
        session.rollback()


def test_claim_then_complete(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        src = _source(session)
        session.flush()
        enqueue(
            session, source_id=src.id, job_type=JobType.FETCH, dedupe_key="dk", scheduled_at=NOW
        )
        session.flush()
        job = claim_next(session, worker_id="w1", now=NOW)
        assert job is not None
        assert job.status == JobStatus.CLAIMED.value
        assert job.attempt == 1
        assert job.claimed_by == "w1"
        complete(session, job, now=NOW)
        assert job.status == JobStatus.DONE.value
        assert job.finished_at is not None
        assert claim_next(session, worker_id="w1", now=NOW) is None  # nothing left
        session.rollback()


def test_fail_retries_then_goes_dead(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        src = _source(session)
        session.flush()
        enqueue(
            session,
            source_id=src.id,
            job_type=JobType.FETCH,
            dedupe_key="dk",
            scheduled_at=NOW,
            max_attempts=2,
        )
        session.flush()
        job = claim_next(session, worker_id="w", now=NOW)  # attempt 1
        assert job is not None
        fail(session, job, now=NOW, error=f"HTTP 500 from https://x.test/?api_key={'X' * 8}")
        assert job.status == JobStatus.PENDING.value
        assert job.scheduled_at > NOW  # backoff scheduled in the future
        assert "api_key" not in (job.last_error or "")  # sanitized before storage
        retry_at = job.scheduled_at
        again = claim_next(session, worker_id="w", now=retry_at)  # attempt 2
        assert again is not None
        assert again.id == job.id
        assert again.attempt == 2
        fail(session, again, now=retry_at)
        assert again.status == JobStatus.DEAD.value  # over max_attempts -> DLQ
        session.rollback()


def test_reclaim_stale_claim(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        src = _source(session)
        session.flush()
        enqueue(
            session, source_id=src.id, job_type=JobType.FETCH, dedupe_key="dk", scheduled_at=NOW
        )
        session.flush()
        job = claim_next(session, worker_id="w", now=NOW)
        assert job is not None
        much_later = NOW + timedelta(hours=1)
        reclaimed = reclaim_stale(session, now=much_later, visibility_timeout=timedelta(minutes=15))
        session.refresh(job)
        assert reclaimed == 1
        assert job.status == JobStatus.PENDING.value
        assert job.claimed_by is None
        session.rollback()


def test_requeue_dead_resets_to_pending(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        src = _source(session)
        session.flush()
        job = enqueue(
            session,
            source_id=src.id,
            job_type=JobType.FETCH,
            dedupe_key="rq",
            scheduled_at=NOW,
            max_attempts=1,
        )
        session.flush()
        claimed = claim_next(session, worker_id="w", now=NOW)  # attempt 1 == max_attempts
        assert claimed is not None
        fail(session, claimed, now=NOW, error="boom")  # over max_attempts -> dead
        assert claimed.status == JobStatus.DEAD.value

        later = NOW + timedelta(hours=1)
        assert requeue_dead(session, now=later, job_id=job.id) == 1
        session.refresh(job)
        assert job.status == JobStatus.PENDING.value
        assert job.attempt == 0
        assert job.claimed_at is None and job.claimed_by is None
        assert job.last_error is None
        assert job.finished_at is None
        assert job.scheduled_at == later
        # idempotent: the job is no longer dead, so a second requeue affects nothing.
        assert requeue_dead(session, now=later, job_id=job.id) == 0
        session.rollback()


def test_skip_locked_across_two_workers(migrated_engine: Engine) -> None:
    marker = "queue_skiplocked"
    try:
        with Session(migrated_engine) as setup:
            src = _source(setup)
            setup.flush()
            for n in (1, 2):
                enqueue(
                    setup,
                    source_id=src.id,
                    job_type=JobType.FETCH,
                    dedupe_key=f"{marker}:{n}",
                    scheduled_at=NOW,
                )
            setup.commit()  # commit so a second connection can see them
        with Session(migrated_engine) as worker_a, Session(migrated_engine) as worker_b:
            a = claim_next(worker_a, worker_id="a", now=NOW)  # locks one row
            b = claim_next(worker_b, worker_id="b", now=NOW)  # SKIP LOCKED -> the other
            assert a is not None
            assert b is not None
            assert a.id != b.id
            worker_a.rollback()
            worker_b.rollback()
    finally:
        # Derive the name from the fixture (not a literal) so a fixture rename can't leak a row.
        src_name = load_source_yaml(FIX / "approved_rss.yaml").to_record().name
        with Session(migrated_engine) as cleanup:
            cleanup.execute(delete(CrawlJob).where(CrawlJob.dedupe_key.like(f"{marker}:%")))
            cleanup.execute(delete(Source).where(Source.name == src_name))
            cleanup.commit()
