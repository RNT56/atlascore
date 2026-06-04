"""Integration: the fetch worker end-to-end (B4 done-when) against Postgres 16 + respx.

Fetches an RSS feed (mocked HTTP, injected public resolver — no live network), captures the raw
payload gzipped on disk + a `raw_document` row, enqueues one `parse` job per GUID, and ignores
duplicate GUIDs on re-fetch. Commits (the worker uses its own sessions) and cleans up after.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import httpx
import pytest
import respx
from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session

from atlascore.connectors_sdk import SafeFetcher
from atlascore.core.clock import FixedClock
from atlascore.schemas.crawl_job import JobStatus, JobType
from atlascore.schemas.source import SourceYAML
from atlascore.storage.postgres.models import CrawlJob, RawDocument, Source
from atlascore.storage.queue import enqueue
from atlascore.storage.rawstore import RawStore
from atlascore.storage.sources import get_source, upsert_source
from atlascore.worker.run import run_one_fetch

pytestmark = pytest.mark.integration

NOW = datetime(2026, 6, 3, 12, 0, 0, tzinfo=UTC)
FEED_URL = "https://feed.example.test/rss"
FEED = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>Worker test</title>
  <item><title>A</title><guid>guid-a</guid><link>https://x.test/a</link></item>
  <item><title>B</title><guid>guid-b</guid><link>https://x.test/b</link></item>
</channel></rss>"""

SOURCE_YAML = {
    "id": "worker_rss_test",
    "name": "Worker RSS Test",
    "category": "news_rss",
    "access_method": "rss",
    "base_url": FEED_URL,
    "enabled": True,
    "policy": {"status": "approved", "robots": "respect", "allowed_uses": ["internal_search"]},
    "rate_limit": {"max_requests": 10, "window_seconds": 1},
    "freshness": {"target": "24h", "warn_after": "36h", "error_after": "72h"},
    "retention": {"raw": "2y", "normalized": "permanent"},
    "credentials": {"ref": None},
    "fetch": {
        "connector": "atlascore.connectors.rss.feed:RssConnector",
        "schedule": "*/30 * * * *",
        "timeout_seconds": 30,
    },
}


def _fetcher() -> SafeFetcher:
    return SafeFetcher(
        client=httpx.Client(),
        clock=FixedClock(NOW),
        resolver=lambda _host: ["93.184.216.34"],  # public -> SSRF passes
        sleep=lambda _seconds: None,  # don't actually sleep on rate-limit spacing
    )


@respx.mock
def test_fetch_worker_captures_raw_and_ignores_dup_guids(
    migrated_engine: Engine, tmp_path: Path
) -> None:
    respx.get(FEED_URL).mock(
        return_value=httpx.Response(
            200, content=FEED, headers={"content-type": "application/rss+xml", "etag": '"v1"'}
        )
    )
    raw_store = RawStore(tmp_path)
    clock = FixedClock(NOW)
    src_id: UUID | None = None
    try:
        with Session(migrated_engine) as session, session.begin():
            upsert_source(session, SourceYAML.model_validate(SOURCE_YAML).to_record())
        with Session(migrated_engine) as session:
            registered = get_source(session, "worker_rss_test")
            assert registered is not None
            src_id = registered.id

        # First fetch: capture raw + enqueue a parse job per GUID.
        with Session(migrated_engine) as session, session.begin():
            enqueue(
                session,
                source_id=src_id,
                job_type=JobType.FETCH,
                dedupe_key="fetch:worker_rss_test:1",
                scheduled_at=NOW,
            )
        result = run_one_fetch(
            migrated_engine, fetcher=_fetcher(), raw_store=raw_store, clock=clock
        )
        assert result is not None
        assert result.error is None
        assert result.not_modified is False
        assert result.new_entries == 2
        assert result.total_entries == 2

        with Session(migrated_engine) as session:
            raws = session.scalars(select(RawDocument).where(RawDocument.source_id == src_id)).all()
            assert len(raws) == 1
            assert raws[0].mime_type == "application/rss+xml"
            assert (tmp_path / raws[0].path).exists()  # raw gzipped on disk
            parse_jobs = session.scalars(
                select(CrawlJob).where(
                    CrawlJob.source_id == src_id, CrawlJob.job_type == JobType.PARSE.value
                )
            ).all()
            assert {j.payload["guid"] for j in parse_jobs if j.payload} == {"guid-a", "guid-b"}
            fetch_jobs = session.scalars(
                select(CrawlJob).where(
                    CrawlJob.source_id == src_id, CrawlJob.job_type == JobType.FETCH.value
                )
            ).all()
            assert all(j.status == JobStatus.DONE.value for j in fetch_jobs)

        # Re-fetch the same feed: duplicate GUIDs are ignored (no new parse jobs, raw deduped).
        with Session(migrated_engine) as session, session.begin():
            enqueue(
                session,
                source_id=src_id,
                job_type=JobType.FETCH,
                dedupe_key="fetch:worker_rss_test:2",
                scheduled_at=NOW,
            )
        again = run_one_fetch(migrated_engine, fetcher=_fetcher(), raw_store=raw_store, clock=clock)
        assert again is not None
        assert again.new_entries == 0  # all GUIDs already enqueued
        assert again.total_entries == 2

        with Session(migrated_engine) as session:
            parse_count = len(
                session.scalars(
                    select(CrawlJob).where(
                        CrawlJob.source_id == src_id, CrawlJob.job_type == JobType.PARSE.value
                    )
                ).all()
            )
            raw_count = len(
                session.scalars(select(RawDocument).where(RawDocument.source_id == src_id)).all()
            )
            assert parse_count == 2  # no duplicates
            assert raw_count == 1  # content-addressed raw deduped
    finally:
        if src_id is not None:
            with Session(migrated_engine) as session:
                session.execute(delete(RawDocument).where(RawDocument.source_id == src_id))
                session.execute(delete(CrawlJob).where(CrawlJob.source_id == src_id))
                session.execute(delete(Source).where(Source.id == src_id))
                session.commit()
