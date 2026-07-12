"""Integration: the parse worker end-to-end (B5 done-when) against Postgres 16 + respx.

Drives the full B4->B5 chain with no live network: fetch an RSS feed (mocked HTTP, injected
public resolver) -> capture raw -> enqueue parse jobs -> run the parse worker -> assert
canonical ``document`` rows are created (sanitized body, attribution, content hash), that the
parse is idempotent (re-parse = unchanged, last-write-wins), and that the FTS index is usable.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import httpx
import pytest
import respx
from sqlalchemy import Engine, delete, func, select, text
from sqlalchemy.orm import Session

from atlascore.connectors_sdk import SafeFetcher
from atlascore.core.clock import FixedClock
from atlascore.schemas.crawl_job import JobStatus, JobType
from atlascore.schemas.source import SourceYAML
from atlascore.storage.postgres.models import CrawlJob, Document, RawDocument, Source
from atlascore.storage.queue import enqueue
from atlascore.storage.rawstore import RawStore
from atlascore.storage.sources import get_source, upsert_source
from atlascore.worker.parse import reparse_raw_document, run_one_parse
from atlascore.worker.run import run_one_fetch

pytestmark = pytest.mark.integration

NOW = datetime(2026, 6, 4, 12, 0, 0, tzinfo=UTC)
FEED_URL = "https://feed.example.test/press"
FEED = (Path(__file__).parent.parent / "fixtures" / "rss" / "sample_press.xml").read_bytes()

SOURCE_YAML = {
    "id": "parse_worker_test",
    "name": "Parse Worker Test",
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
        sleep=lambda _seconds: None,
        pin_connections=False,
    )


@respx.mock
def test_fetch_then_parse_creates_documents(migrated_engine: Engine, tmp_path: Path) -> None:
    respx.get(FEED_URL).mock(
        return_value=httpx.Response(
            200, content=FEED, headers={"content-type": "application/rss+xml"}
        )
    )
    raw_store = RawStore(tmp_path)
    clock = FixedClock(NOW)
    src_id: UUID | None = None
    try:
        with Session(migrated_engine) as session, session.begin():
            upsert_source(session, SourceYAML.model_validate(SOURCE_YAML).to_record())
        with Session(migrated_engine) as session:
            registered = get_source(session, "parse_worker_test")
            assert registered is not None
            src_id = registered.id

        # B4: fetch -> raw + one parse job per GUID.
        with Session(migrated_engine) as session, session.begin():
            enqueue(
                session,
                source_id=src_id,
                job_type=JobType.FETCH,
                dedupe_key="fetch:parse_worker_test:1",
                scheduled_at=NOW,
            )
        fetch_result = run_one_fetch(
            migrated_engine, fetcher=_fetcher(), raw_store=raw_store, clock=clock
        )
        assert fetch_result is not None and fetch_result.error is None
        assert fetch_result.new_entries == 2

        # B5: drain the parse jobs.
        first = run_one_parse(migrated_engine, raw_store=raw_store, clock=clock)
        second = run_one_parse(migrated_engine, raw_store=raw_store, clock=clock)
        third = run_one_parse(migrated_engine, raw_store=raw_store, clock=clock)
        assert first is not None and first.error is None
        assert second is not None and second.error is None
        assert third is None  # queue drained
        assert (first.documents_created + second.documents_created) == 2

        with Session(migrated_engine) as session:
            docs = session.scalars(
                select(Document)
                .where(Document.source_id == src_id)
                .order_by(Document.canonical_url)
            ).all()
            assert len(docs) == 2
            by_url = {d.canonical_url: d for d in docs}
            a = by_url["https://example.test/press/a"]
            assert a.title == "Policy statement & rate decision"
            assert a.body_text == "The committee decided to hold rates steady.\nRead more »"
            assert "steal" not in (a.body_text or "")  # script sanitized end-to-end
            assert a.parser_version == "article@1.0.0"
            assert a.published_at == datetime(2026, 6, 3, 18, 0, 0, tzinfo=UTC)
            assert a.raw_document_id is not None
            assert a.content_hash.startswith("sha256:")

            # All parse jobs completed.
            parse_jobs = session.scalars(
                select(CrawlJob).where(
                    CrawlJob.source_id == src_id, CrawlJob.job_type == JobType.PARSE.value
                )
            ).all()
            assert all(j.status == JobStatus.DONE.value for j in parse_jobs)

            # FTS index is usable (B6 builds the CLI query on top of this).
            hits = session.scalar(
                select(func.count())
                .select_from(Document)
                .where(
                    Document.source_id == src_id,
                    text(
                        "to_tsvector('simple', coalesce(body_text,'')) "
                        "@@ to_tsquery('simple', 'rates')"
                    ),
                )
            )
            assert hits == 1

            raw_id = docs[0].raw_document_id

        # Re-parse the whole raw: reproducible + idempotent (last-write-wins, R2).
        again = reparse_raw_document(migrated_engine, raw_id, raw_store=raw_store, clock=clock)
        assert again.documents_created == 0
        assert again.documents_unchanged == 2

        with Session(migrated_engine) as session:
            count = session.scalar(
                select(func.count()).select_from(Document).where(Document.source_id == src_id)
            )
            assert count == 2  # no duplicates on re-parse
    finally:
        if src_id is not None:
            with Session(migrated_engine) as session:
                session.execute(delete(Document).where(Document.source_id == src_id))
                session.execute(delete(RawDocument).where(RawDocument.source_id == src_id))
                session.execute(delete(CrawlJob).where(CrawlJob.source_id == src_id))
                session.execute(delete(Source).where(Source.id == src_id))
                session.commit()


NOLINK_FEED = (
    b'<?xml version="1.0"?><rss version="2.0"><channel><title>NoLink</title>'
    b"<item><title>Same</title><guid>g1</guid><description>identical body</description></item>"
    b"<item><title>Same</title><guid>g2</guid><description>identical body</description></item>"
    b"</channel></rss>"
)


@respx.mock
def test_distinct_guid_linkless_items_not_collapsed(
    migrated_engine: Engine, tmp_path: Path
) -> None:
    """Two link-less items with identical title+body but distinct GUIDs stay distinct documents
    (review finding 5: the GUID is folded into the null-URL dedupe fingerprint)."""
    url = "https://feed.example.test/nolink"
    respx.get(url).mock(
        return_value=httpx.Response(200, content=NOLINK_FEED, headers={"content-type": "text/xml"})
    )
    raw_store = RawStore(tmp_path)
    clock = FixedClock(NOW)
    yaml = {**SOURCE_YAML, "id": "parse_nolink_test", "name": "Parse NoLink Test", "base_url": url}
    src_id: UUID | None = None
    try:
        with Session(migrated_engine) as session, session.begin():
            upsert_source(session, SourceYAML.model_validate(yaml).to_record())
        with Session(migrated_engine) as session:
            registered = get_source(session, "parse_nolink_test")
            assert registered is not None
            src_id = registered.id
        with Session(migrated_engine) as session, session.begin():
            enqueue(
                session,
                source_id=src_id,
                job_type=JobType.FETCH,
                dedupe_key="fetch:parse_nolink_test:1",
                scheduled_at=NOW,
            )
        assert run_one_fetch(migrated_engine, fetcher=_fetcher(), raw_store=raw_store, clock=clock)
        while run_one_parse(migrated_engine, raw_store=raw_store, clock=clock) is not None:
            pass
        with Session(migrated_engine) as session:
            docs = session.scalars(select(Document).where(Document.source_id == src_id)).all()
            assert len(docs) == 2  # distinct GUIDs -> two rows, not collapsed into one
            assert all(d.canonical_url is None for d in docs)
    finally:
        if src_id is not None:
            with Session(migrated_engine) as session:
                session.execute(delete(Document).where(Document.source_id == src_id))
                session.execute(delete(RawDocument).where(RawDocument.source_id == src_id))
                session.execute(delete(CrawlJob).where(CrawlJob.source_id == src_id))
                session.execute(delete(Source).where(Source.id == src_id))
                session.commit()


def _feed(body: str) -> bytes:
    return (
        '<?xml version="1.0"?>'
        '<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">'
        "<channel><title>Upd</title><item><title>Item</title>"
        "<guid>https://example.test/upd</guid><link>https://example.test/upd</link>"
        f"<content:encoded><![CDATA[<p>{body}</p>]]></content:encoded>"
        "</item></channel></rss>"
    ).encode()


@respx.mock
def test_same_guid_in_place_edit_is_reparsed(migrated_engine: Engine, tmp_path: Path) -> None:
    """A publisher edits an item in place under a stable GUID -> the new body is re-parsed
    (review finding 4: the content-aware parse dedupe_key re-enqueues a parse job)."""
    url = "https://feed.example.test/upd"
    respx.get(url).mock(
        side_effect=[
            httpx.Response(
                200, content=_feed("version one"), headers={"content-type": "application/rss+xml"}
            ),
            httpx.Response(
                200, content=_feed("version two"), headers={"content-type": "application/rss+xml"}
            ),
        ]
    )
    raw_store = RawStore(tmp_path)
    clock = FixedClock(NOW)
    yaml = {**SOURCE_YAML, "id": "parse_update_test", "name": "Parse Update Test", "base_url": url}
    src_id: UUID | None = None
    try:
        with Session(migrated_engine) as session, session.begin():
            upsert_source(session, SourceYAML.model_validate(yaml).to_record())
        with Session(migrated_engine) as session:
            registered = get_source(session, "parse_update_test")
            assert registered is not None
            src_id = registered.id

        def fetch_and_parse(dedupe: str) -> None:
            with Session(migrated_engine) as session, session.begin():
                enqueue(
                    session,
                    source_id=src_id,
                    job_type=JobType.FETCH,
                    dedupe_key=dedupe,
                    scheduled_at=NOW,
                )
            assert run_one_fetch(
                migrated_engine, fetcher=_fetcher(), raw_store=raw_store, clock=clock
            )
            while run_one_parse(migrated_engine, raw_store=raw_store, clock=clock) is not None:
                pass

        fetch_and_parse("fetch:parse_update_test:1")
        fetch_and_parse("fetch:parse_update_test:2")

        with Session(migrated_engine) as session:
            docs = session.scalars(select(Document).where(Document.source_id == src_id)).all()
            assert len(docs) == 1  # same canonical_url -> last-write-wins, not duplicated
            assert docs[0].body_text == "version two"  # the edit was re-parsed, not stranded
    finally:
        if src_id is not None:
            with Session(migrated_engine) as session:
                session.execute(delete(Document).where(Document.source_id == src_id))
                session.execute(delete(RawDocument).where(RawDocument.source_id == src_id))
                session.execute(delete(CrawlJob).where(CrawlJob.source_id == src_id))
                session.execute(delete(Source).where(Source.id == src_id))
                session.commit()
