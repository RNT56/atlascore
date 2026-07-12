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
        pin_connections=False,
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


@respx.mock
def test_sec_primary_filings_are_stored_and_parsed_without_parse_worker_network(
    migrated_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import atlascore.connectors.sec.submissions as sec_mod
    import atlascore.worker.parse as parse_mod
    from atlascore.parsers.filing import ParsedFiling

    monkeypatch.setattr(sec_mod, "tika_available", lambda: True)

    class _FakeParser:
        def parse(self, data: bytes, *, content_type: str | None = None) -> ParsedFiling:
            return ParsedFiling(body_text=f"Extracted primary filing {data.decode()}.")

    monkeypatch.setattr(parse_mod, "_load_local_content_parser", lambda _ref: _FakeParser())
    respx.get(url__regex=r"https://data\.sec\.gov/submissions/.*").mock(
        return_value=httpx.Response(
            200, content=SEC_JSON, headers={"content-type": "application/json"}
        )
    )

    def filing_response(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=request.url.path.encode(),
            headers={"content-type": "text/html"},
        )

    respx.get(url__regex=r"https://www\.sec\.gov/Archives/edgar/data/.*").mock(
        side_effect=filing_response
    )
    configured = dict(SEC_YAML)
    configured["id"] = "sec_full_filing_test"
    configured["name"] = "SEC Full Filing Test"
    configured["fetch"] = {
        **SEC_YAML["fetch"],
        "params": {"cik": "320193", "extract_filings": "true"},
    }
    raw_store = RawStore(tmp_path)
    source_id: UUID | None = None
    try:
        with Session(migrated_engine) as session, session.begin():
            upsert_source(session, SourceYAML.model_validate(configured).to_record())
        with Session(migrated_engine) as session:
            source = get_source(session, "sec_full_filing_test")
            assert source is not None
            source_id = source.id
        with Session(migrated_engine) as session, session.begin():
            enqueue(
                session,
                source_id=source_id,
                job_type=JobType.FETCH,
                dedupe_key="fetch:sec-full-filing:1",
                scheduled_at=NOW,
            )
        fetched = run_one_fetch(
            migrated_engine, fetcher=_fetcher(), raw_store=raw_store, clock=FixedClock(NOW)
        )
        assert fetched is not None and fetched.error is None
        _drain_parse(migrated_engine, raw_store, FixedClock(NOW))

        with Session(migrated_engine) as session:
            raw_rows = session.scalars(
                select(RawDocument).where(RawDocument.source_id == source_id)
            ).all()
            filing_rows = [row for row in raw_rows if row.parser_ref]
            assert len(filing_rows) == 3
            assert all(
                row.origin_url and "sec.gov/Archives" in row.origin_url for row in filing_rows
            )
            documents = session.scalars(
                select(Document).where(Document.source_id == source_id)
            ).all()
            assert len(documents) == 3
            assert all(doc.parser_version == "filing@1.0.0" for doc in documents)
            assert all("Extracted primary filing" in (doc.body_text or "") for doc in documents)
    finally:
        if source_id is not None:
            _cleanup(migrated_engine, [source_id])
