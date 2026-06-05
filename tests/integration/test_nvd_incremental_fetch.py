"""Integration: B20 worker injects + persists the NVD incremental cursor (PG16 + respx).

The first fetch (no cursor) sends no window and persists `lastModStartDate`; the second fetch reads
that cursor, sends `lastModStartDate` + `lastModEndDate` (now), and re-persists. No live network.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session

from atlascore.connectors_sdk import SafeFetcher
from atlascore.core.clock import FixedClock
from atlascore.schemas.crawl_job import JobType
from atlascore.schemas.source import SourceYAML
from atlascore.storage.checkpoints import get_checkpoint
from atlascore.storage.postgres.models import CrawlJob, RawDocument, Source
from atlascore.storage.queue import enqueue
from atlascore.storage.rawstore import RawStore
from atlascore.storage.sources import get_source, upsert_source
from atlascore.worker.run import run_one_fetch

pytestmark = pytest.mark.integration

NOW = datetime(2026, 6, 3, 12, 0, 0, tzinfo=UTC)
BASE = "https://services.nvd.nist.gov"
SLUG = "nvd_incr_it"
PAYLOAD = (
    b'{"vulnerabilities":[{"cve":{"id":"CVE-2026-1","published":"2026-06-01T00:00:00.000",'
    b'"lastModified":"2026-06-02T08:00:00.000",'
    b'"descriptions":[{"lang":"en","value":"x"}]}}]}'
)

SOURCE_YAML = {
    "id": SLUG,
    "name": "NVD incr it",
    "category": "security_cve",
    "access_method": "api",
    "base_url": BASE,
    "enabled": True,
    "policy": {"status": "approved", "robots": "respect", "allowed_uses": ["internal_search"]},
    "rate_limit": {"max_requests": 50, "window_seconds": 30},
    "freshness": {"target": "24h", "warn_after": "48h", "error_after": "96h"},
    "retention": {"raw": "2y", "normalized": "permanent"},
    "credentials": {"ref": None},
    "fetch": {
        "connector": "atlascore.connectors.nvd.cves:NvdCveConnector",
        "schedule": "0 * * * *",
        "timeout_seconds": 30,
    },
}


def _cleanup(session: Session) -> None:
    ids = list(
        session.scalars(select(Source.id).where(Source.config["slug"].astext.like(f"{SLUG}%")))
    )
    if ids:
        # raw_document.crawl_job_id references crawl_job → delete raw first.
        session.execute(delete(RawDocument).where(RawDocument.source_id.in_(ids)))
        session.execute(delete(CrawlJob).where(CrawlJob.source_id.in_(ids)))
        session.execute(delete(Source).where(Source.id.in_(ids)))


def _fetcher() -> SafeFetcher:
    return SafeFetcher(
        client=httpx.Client(),
        clock=FixedClock(NOW),
        resolver=lambda _host: ["93.184.216.34"],
        sleep=lambda _s: None,
    )


def _enqueue_fetch(engine: Engine, src_id: object, n: int) -> None:
    with Session(engine) as session, session.begin():
        enqueue(
            session,
            source_id=src_id,
            job_type=JobType.FETCH,
            dedupe_key=f"fetch:{SLUG}:{n}",
            scheduled_at=NOW,
        )


@respx.mock
def test_nvd_cursor_injected_and_persisted(migrated_engine: Engine, tmp_path: Path) -> None:
    route = respx.get(url__startswith=BASE).mock(
        return_value=httpx.Response(
            200, content=PAYLOAD, headers={"content-type": "application/json"}
        )
    )
    raw_store = RawStore(tmp_path)
    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)
        upsert_source(session, SourceYAML.model_validate(SOURCE_YAML).to_record())
    with Session(migrated_engine) as session:
        src_id = get_source(session, SLUG).id  # type: ignore[union-attr]

    # First fetch: no prior cursor → no lastModStartDate in the query; cursor persisted afterwards.
    _enqueue_fetch(migrated_engine, src_id, 1)
    run_one_fetch(migrated_engine, fetcher=_fetcher(), raw_store=raw_store, clock=FixedClock(NOW))
    assert "lastModStartDate" not in str(route.calls[0].request.url)
    with Session(migrated_engine) as session:
        assert get_checkpoint(session, src_id) == {"lastModStartDate": "2026-06-02T08:00:00.000"}

    # Second fetch: prior cursor present → request carries both window bounds.
    _enqueue_fetch(migrated_engine, src_id, 2)
    run_one_fetch(migrated_engine, fetcher=_fetcher(), raw_store=raw_store, clock=FixedClock(NOW))
    second_url = str(route.calls.last.request.url)
    assert "lastModStartDate=2026-06-02" in second_url
    assert "lastModEndDate=2026-06-03" in second_url  # end = the worker's fetch-time now

    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)
