"""Integration: B10 — SEC filings + NVD CVEs become searchable documents (PG16).

The B10 done-when: register a SEC submissions source and an NVD CVE source (both `access_method=api`
that produce **documents**), run the single fetch worker (which dispatches them to the B5 document
pipeline by `output_kind`), drain the parse jobs, and assert `search_documents` finds the filing
and the CVE. No live network (respx-mocked, injected public resolver).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import httpx
import pytest
import respx
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session

from atlascore.connectors_sdk import SafeFetcher
from atlascore.core.clock import FixedClock
from atlascore.schemas.crawl_job import JobType
from atlascore.schemas.source import SourceYAML
from atlascore.storage.postgres.models import CrawlJob, Document, RawDocument, Source
from atlascore.storage.queue import enqueue
from atlascore.storage.rawstore import RawStore
from atlascore.storage.search import search_documents
from atlascore.storage.sources import get_source, upsert_source
from atlascore.worker.parse import run_one_parse
from atlascore.worker.run import run_one_fetch

pytestmark = pytest.mark.integration

NOW = datetime(2026, 6, 4, 12, 0, tzinfo=UTC)
_FIX = Path(__file__).parent.parent / "fixtures"
SEC_JSON = (_FIX / "sec" / "apple_submissions.json").read_bytes()
NVD_JSON = (_FIX / "nvd" / "cves_sample.json").read_bytes()

SEC_YAML = {
    "id": "sec_b10_test",
    "name": "SEC B10 Test",
    "category": "finance_filings",
    "access_method": "api",
    "base_url": "https://data.sec.gov",
    "enabled": True,
    "policy": {"status": "approved", "robots": "respect", "allowed_uses": ["internal_search"]},
    "rate_limit": {"max_requests": 10, "window_seconds": 1},
    "freshness": {"target": "24h", "warn_after": "36h", "error_after": "72h"},
    "retention": {"raw": "2y", "normalized": "permanent"},
    "credentials": {"ref": None},
    "fetch": {
        "connector": "atlascore.connectors.sec.submissions:SecSubmissionsConnector",
        "schedule": "0 6 * * *",
        "timeout_seconds": 30,
        "params": {"cik": "320193"},
    },
}
NVD_YAML = {
    "id": "nvd_b10_test",
    "name": "NVD B10 Test",
    "category": "security_cve",
    "access_method": "api",
    "base_url": "https://services.nvd.nist.gov",
    "enabled": True,
    "policy": {"status": "approved", "robots": "respect", "allowed_uses": ["internal_search"]},
    "rate_limit": {"max_requests": 5, "window_seconds": 30},
    "freshness": {"target": "24h", "warn_after": "36h", "error_after": "72h"},
    "retention": {"raw": "2y", "normalized": "permanent"},
    "credentials": {"ref": None},
    "fetch": {
        "connector": "atlascore.connectors.nvd.cves:NvdCveConnector",
        "schedule": "0 * * * *",
        "timeout_seconds": 30,
        "params": {},
    },
}


def _fetcher() -> SafeFetcher:
    return SafeFetcher(
        client=httpx.Client(),
        clock=FixedClock(NOW),
        resolver=lambda _host: ["93.184.216.34"],
        sleep=lambda _s: None,
    )


def _drain_parse(engine: Engine, raw_store: RawStore, clock: FixedClock) -> None:
    while run_one_parse(engine, raw_store=raw_store, clock=clock) is not None:
        pass


def _cleanup(engine: Engine, ids: list[UUID]) -> None:
    with Session(engine) as session:
        for src_id in ids:
            session.execute(delete(Document).where(Document.source_id == src_id))
            session.execute(delete(RawDocument).where(RawDocument.source_id == src_id))
            session.execute(delete(CrawlJob).where(CrawlJob.source_id == src_id))
            session.execute(delete(Source).where(Source.id == src_id))
        session.commit()


@respx.mock
def test_sec_and_nvd_documents_are_searchable(migrated_engine: Engine, tmp_path: Path) -> None:
    respx.get(url__regex=r"https://data\.sec\.gov/submissions/.*").mock(
        return_value=httpx.Response(
            200, content=SEC_JSON, headers={"content-type": "application/json"}
        )
    )
    respx.get(url__regex=r"https://services\.nvd\.nist\.gov/rest/json/cves.*").mock(
        return_value=httpx.Response(
            200, content=NVD_JSON, headers={"content-type": "application/json"}
        )
    )
    raw_store = RawStore(tmp_path)
    clock = FixedClock(NOW)
    ids: list[UUID] = []
    try:
        # Register both sources.
        with Session(migrated_engine) as session, session.begin():
            upsert_source(session, SourceYAML.model_validate(SEC_YAML).to_record())
            upsert_source(session, SourceYAML.model_validate(NVD_YAML).to_record())
        with Session(migrated_engine) as session:
            sec_id = get_source(session, "sec_b10_test").id  # type: ignore[union-attr]
            nvd_id = get_source(session, "nvd_b10_test").id  # type: ignore[union-attr]
            ids = [sec_id, nvd_id]

        # Fetch + parse both (the fetch worker dispatches api-document sources to the doc path).
        for src_id, key in ((sec_id, "fetch:sec_b10:1"), (nvd_id, "fetch:nvd_b10:1")):
            with Session(migrated_engine) as session, session.begin():
                enqueue(
                    session,
                    source_id=src_id,
                    job_type=JobType.FETCH,
                    dedupe_key=key,
                    scheduled_at=NOW,
                )
            result = run_one_fetch(
                migrated_engine, fetcher=_fetcher(), raw_store=raw_store, clock=clock
            )
            assert result is not None and result.error is None
        _drain_parse(migrated_engine, raw_store, clock)

        with Session(migrated_engine) as session:
            assert (
                session.scalar(
                    select(func.count()).select_from(Document).where(Document.source_id == sec_id)
                )
                == 3
            )
            assert (
                session.scalar(
                    select(func.count()).select_from(Document).where(Document.source_id == nvd_id)
                )
                == 2
            )

            # SEC filing is searchable.
            sec_hits = search_documents(session, "annual report", source="sec_b10_test")
            assert any(h.title == "10-K — Apple Inc." for h in sec_hits)
            assert all("sec.gov" in (h.canonical_url or "") for h in sec_hits)

            # NVD CVEs are searchable by their description text.
            cve_hits = search_documents(session, "buffer overflow", source="nvd_b10_test")
            assert [h.title for h in cve_hits] == ["CVE-2026-10001"]
            assert cve_hits[0].canonical_url == "https://nvd.nist.gov/vuln/detail/CVE-2026-10001"
            assert {h.title for h in search_documents(session, "injection")} >= {"CVE-2026-10002"}
    finally:
        if ids:
            _cleanup(migrated_engine, ids)
