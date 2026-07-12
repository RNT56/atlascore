"""Integration: B19 worker loop drains fetch→parse→enrich in one bounded run (PG16 + respx).

Seeds an approved RSS source + a fetch job, then runs the loop (fetch role under the single-fetch
lock, with an injected stub-resolver fetcher) for a bounded number of passes and asserts the chain
drains to ``document`` rows. No live network.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session

from atlascore.connectors_sdk import SafeFetcher
from atlascore.core.clock import FixedClock
from atlascore.core.settings import Settings
from atlascore.schemas.crawl_job import JobStatus, JobType
from atlascore.schemas.source import SourceYAML
from atlascore.storage.postgres.models import (
    CrawlJob,
    Document,
    Embedding,
    Mention,
    RawDocument,
    Source,
    SourceHealth,
)
from atlascore.storage.queue import enqueue
from atlascore.storage.rawstore import RawStore
from atlascore.storage.sources import get_source, upsert_source
from atlascore.worker.loop import run_worker_loop

pytestmark = pytest.mark.integration

NOW = datetime(2026, 6, 3, 12, 0, 0, tzinfo=UTC)
FEED_URL = "https://feed.example.test/loop"
SLUG = "loop_drain_it"
FEED = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>Loop drain</title>
  <item><title>Item A</title><guid>loop-a</guid><link>https://x.test/a</link>
    <description>The first article body, long enough to parse.</description></item>
  <item><title>Item B</title><guid>loop-b</guid><link>https://x.test/b</link>
    <description>The second article body, also parseable.</description></item>
</channel></rss>"""

SOURCE_YAML = {
    "id": SLUG,
    "name": "Loop drain source",
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


def _cleanup(session: Session) -> None:
    ids = list(
        session.scalars(select(Source.id).where(Source.config["slug"].astext.like(f"{SLUG}%")))
    )
    if ids:
        doc_ids = list(session.scalars(select(Document.id).where(Document.source_id.in_(ids))))
        if doc_ids:  # enrichment children reference document → delete them first (FK)
            session.execute(delete(Embedding).where(Embedding.document_id.in_(doc_ids)))
            session.execute(delete(Mention).where(Mention.document_id.in_(doc_ids)))
        session.execute(delete(Document).where(Document.source_id.in_(ids)))
        session.execute(delete(RawDocument).where(RawDocument.source_id.in_(ids)))
        session.execute(delete(CrawlJob).where(CrawlJob.source_id.in_(ids)))
        session.execute(delete(SourceHealth).where(SourceHealth.source_id.in_(ids)))
        session.execute(delete(Source).where(Source.id.in_(ids)))


def _fetcher() -> SafeFetcher:
    return SafeFetcher(
        client=httpx.Client(),
        clock=FixedClock(NOW),
        resolver=lambda _host: ["93.184.216.34"],  # public → SSRF passes
        sleep=lambda _seconds: None,
        pin_connections=False,
    )


@respx.mock
def test_loop_drains_fetch_parse_enrich(migrated_engine: Engine, tmp_path: Path) -> None:
    respx.get(FEED_URL).mock(
        return_value=httpx.Response(
            200, content=FEED, headers={"content-type": "application/rss+xml"}
        )
    )
    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)
        upsert_source(session, SourceYAML.model_validate(SOURCE_YAML).to_record())
    with Session(migrated_engine) as session:
        src_id = get_source(session, SLUG).id  # type: ignore[union-attr]
    with Session(migrated_engine) as session, session.begin():
        enqueue(
            session,
            source_id=src_id,
            job_type=JobType.FETCH,
            dedupe_key=f"fetch:{SLUG}:once",
            scheduled_at=NOW,
        )

    # One bounded loop run: fetch enqueues 2 parse jobs, then parse→enrich drain them.
    result = run_worker_loop(
        migrated_engine,
        roles=("fetch", "parse", "enrich"),
        clock=FixedClock(NOW),
        settings=Settings(worker_idle_backoff_max_seconds=0.01),  # keep idle passes fast in tests
        raw_store=RawStore(tmp_path),
        fetcher=_fetcher(),
        stop_event=threading.Event(),
        max_passes=10,
    )
    assert result.jobs_processed >= 1

    with Session(migrated_engine) as session:
        docs = session.scalar(
            select(func.count()).select_from(Document).where(Document.source_id == src_id)
        )
        assert docs == 2  # both feed items parsed into documents
        fetch_done = session.scalars(
            select(CrawlJob).where(
                CrawlJob.source_id == src_id, CrawlJob.job_type == JobType.FETCH.value
            )
        ).all()
        assert all(j.status == JobStatus.DONE.value for j in fetch_done)

    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)
