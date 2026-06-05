"""Integration: B19 scheduler enqueue + lock + inline housekeeping against Postgres 16.

A due fetchable source gets exactly one bucketed fetch job per tick (idempotent across overlapping
ticks); a disabled source enqueues none (default-deny); the scheduler lock refuses a second holder;
and a single tick runs the inline housekeeping (freshness/alert/partition/audit).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session

from atlascore.alerts.delivery import LogChannel
from atlascore.core.errors import SchedulerBusyError
from atlascore.scheduler.lock import scheduler_active, scheduler_lock
from atlascore.scheduler.run import enqueue_due_fetches, run_one_scheduler_tick
from atlascore.schemas.crawl_job import JobType
from atlascore.schemas.source import SourceYAML
from atlascore.storage.postgres.models import CrawlJob, Source, SourceHealth
from atlascore.storage.sources import get_source, upsert_source

pytestmark = pytest.mark.integration

NOW = datetime(2026, 6, 3, 12, 0, 0, tzinfo=UTC)
TICK = timedelta(seconds=60)
SLUG = "sched_enqueue_it"


def _yaml(slug: str, *, enabled: bool, status: str = "approved") -> dict:
    return {
        "id": slug,
        "name": f"{slug} src",
        "category": "news_rss",
        "access_method": "rss",
        "base_url": "https://feed.example.test/rss",
        "enabled": enabled,
        "policy": {"status": status, "robots": "respect", "allowed_uses": ["internal_search"]},
        "rate_limit": {"max_requests": 10, "window_seconds": 1},
        "freshness": {"target": "24h", "warn_after": "36h", "error_after": "72h"},
        "retention": {"raw": "2y", "normalized": "permanent"},
        "credentials": {"ref": None},
        "fetch": {
            "connector": "atlascore.connectors.rss.feed:RssConnector",
            "schedule": "*/15 * * * *",
            "timeout_seconds": 30,
        },
    }


def _cleanup(session: Session) -> None:
    ids = list(
        session.scalars(select(Source.id).where(Source.config["slug"].astext.like(f"{SLUG}%")))
    )
    if ids:
        session.execute(delete(CrawlJob).where(CrawlJob.source_id.in_(ids)))
        session.execute(delete(SourceHealth).where(SourceHealth.source_id.in_(ids)))
        session.execute(delete(Source).where(Source.id.in_(ids)))


def _fetch_count(session: Session, source_id: object) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(CrawlJob)
            .where(CrawlJob.source_id == source_id, CrawlJob.job_type == JobType.FETCH.value)
        )
        or 0
    )


def test_enqueue_due_idempotent_and_default_deny(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)
        upsert_source(
            session, SourceYAML.model_validate(_yaml(f"{SLUG}_ok", enabled=True)).to_record()
        )
        upsert_source(
            session, SourceYAML.model_validate(_yaml(f"{SLUG}_off", enabled=False)).to_record()
        )
    with Session(migrated_engine) as session:
        ok_id = get_source(session, f"{SLUG}_ok").id  # type: ignore[union-attr]

    # First tick in the bucket enqueues exactly one fetch for the due, fetchable source.
    with Session(migrated_engine) as session, session.begin():
        n1 = enqueue_due_fetches(session, now=NOW, tick=TICK)
    assert n1 == 1
    # A second tick in the SAME bucket is idempotent (same dedupe_key) → 0 new.
    with Session(migrated_engine) as session, session.begin():
        n2 = enqueue_due_fetches(session, now=NOW + timedelta(seconds=5), tick=TICK)
    assert n2 == 0

    with Session(migrated_engine) as session:
        assert _fetch_count(session, ok_id) == 1  # exactly one fetch job total

    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)


def test_scheduler_lock_refuses_second_holder(migrated_engine: Engine) -> None:
    with scheduler_lock(migrated_engine):
        with Session(migrated_engine) as session:
            assert scheduler_active(session) is True
        with pytest.raises(SchedulerBusyError):  # noqa: SIM117
            with scheduler_lock(migrated_engine):
                pass
    with Session(migrated_engine) as session:
        assert scheduler_active(session) is False  # released on exit


def test_tick_runs_inline_housekeeping(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)
    result = run_one_scheduler_tick(migrated_engine, now=NOW, channel=LogChannel())
    # A fresh tick runs every housekeeping task at least once.
    for task in ("freshness", "alert", "partition", "audit"):
        assert task in result.housekeeping_ran
