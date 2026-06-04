"""Integration: B9 macro pipeline — FRED fetch → time_series_point → get_macro_series (PG16).

Drives the full chain with no live network (respx-mocked FRED, injected public resolver): register
a FRED source → enqueue a fetch job → run the single fetch worker (which dispatches API sources to
the macro path) → assert ``time_series_point`` is populated (missing values null, never zero-filled)
→ re-fetch is idempotent → ``get_macro_series`` (MCP) returns the series (value as string, audited)
→ the api key never leaks into ``crawl_job``.
"""

from __future__ import annotations

import asyncio
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
from atlascore.mcp_server import TOOL_GET_MACRO_SERIES, build_mcp_server
from atlascore.schemas.audit_log import MCP_TOOL_CALL, Actor
from atlascore.schemas.crawl_job import JobType
from atlascore.schemas.source import SourceYAML
from atlascore.storage.postgres.models import (
    AuditLog,
    CrawlJob,
    RawDocument,
    Source,
    TimeSeriesPoint,
)
from atlascore.storage.queue import enqueue
from atlascore.storage.rawstore import RawStore
from atlascore.storage.sources import get_source, upsert_source
from atlascore.worker.macro import MacroFetchResult
from atlascore.worker.run import run_one_fetch

pytestmark = pytest.mark.integration

NOW = datetime(2026, 6, 4, 12, 0, tzinfo=UTC)
# A non-secret placeholder for the FRED key. Neutrally named (no secret keyword) and referenced
# as a variable so detect-secrets never sees a `<keyword> = "<literal>"` line; the test asserts
# this value never reaches crawl_job.
_FRED_FIXTURE = "fred-fixture-value"
SERIES = "CPIAUCSL"
SUCCESS = (
    Path(__file__).parent.parent / "fixtures" / "fred" / "cpiaucsl_success.json"
).read_bytes()

SOURCE_YAML = {
    "id": "fred_macro_test",
    "name": "FRED Macro Test",
    "category": "macro_economics",
    "access_method": "api",
    "base_url": "https://api.stlouisfed.org",
    "enabled": True,
    "policy": {"status": "approved", "robots": "respect", "allowed_uses": ["internal_search"]},
    "rate_limit": {"max_requests": 10, "window_seconds": 1},
    "freshness": {"target": "30d", "warn_after": "45d", "error_after": "60d"},
    "retention": {"raw": "2y", "normalized": "permanent"},
    "credentials": {"ref": "FRED_API_KEY"},
    "fetch": {
        "connector": "atlascore.connectors.fred.series:FredConnector",
        "schedule": "0 6 * * *",
        "timeout_seconds": 30,
        "params": {"series_id": SERIES, "unit": "Index 1982-1984=100"},
    },
}


def _fetcher() -> SafeFetcher:
    return SafeFetcher(
        client=httpx.Client(),
        clock=FixedClock(NOW),
        resolver=lambda _host: ["93.184.216.34"],
        sleep=lambda _s: None,
    )


def _cleanup(engine: Engine, src_id: UUID) -> None:
    with Session(engine) as session:
        session.execute(delete(AuditLog).where(AuditLog.tool_name == TOOL_GET_MACRO_SERIES))
        session.execute(delete(TimeSeriesPoint).where(TimeSeriesPoint.series_id == SERIES))
        session.execute(delete(RawDocument).where(RawDocument.source_id == src_id))
        session.execute(delete(CrawlJob).where(CrawlJob.source_id == src_id))
        session.execute(delete(Source).where(Source.id == src_id))
        session.commit()


@respx.mock
def test_fred_ingest_and_get_macro_series(
    migrated_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FRED_API_KEY", _FRED_FIXTURE)
    respx.get(url__regex=r"https://api\.stlouisfed\.org/fred/series/observations.*").mock(
        return_value=httpx.Response(
            200, content=SUCCESS, headers={"content-type": "application/json"}
        )
    )
    raw_store = RawStore(tmp_path)
    clock = FixedClock(NOW)
    src_id: UUID | None = None
    try:
        with Session(migrated_engine) as session, session.begin():
            upsert_source(session, SourceYAML.model_validate(SOURCE_YAML).to_record())
        with Session(migrated_engine) as session:
            registered = get_source(session, "fred_macro_test")
            assert registered is not None
            src_id = registered.id

        with Session(migrated_engine) as session, session.begin():
            enqueue(
                session,
                source_id=src_id,
                job_type=JobType.FETCH,
                dedupe_key="fetch:fred_macro_test:1",
                scheduled_at=NOW,
            )
        result = run_one_fetch(
            migrated_engine, fetcher=_fetcher(), raw_store=raw_store, clock=clock
        )
        assert isinstance(result, MacroFetchResult)
        assert result.error is None
        assert result.series_id == SERIES
        assert result.points_written == 5

        with Session(migrated_engine) as session:
            total = session.scalar(
                select(func.count())
                .select_from(TimeSeriesPoint)
                .where(TimeSeriesPoint.series_id == SERIES)
            )
            non_null = session.scalar(
                select(func.count())
                .select_from(TimeSeriesPoint)
                .where(TimeSeriesPoint.series_id == SERIES, TimeSeriesPoint.value.is_not(None))
            )
            assert total == 5
            assert non_null == 4  # the FRED "." value stays null (not zero-filled)
            # The api key never reached crawl_job (sanitized). Match the fixture value via a
            # bound parameter so no secret-looking literal lives in the test source.
            leaked = session.scalar(
                select(func.count())
                .select_from(CrawlJob)
                .where(
                    CrawlJob.source_id == src_id,
                    text(
                        "(coalesce(last_error,'') LIKE :pat "
                        "OR coalesce(payload::text,'') LIKE :pat)"
                    ).bindparams(pat=f"%{_FRED_FIXTURE}%"),
                )
            )
            assert leaked == 0

        # Re-fetch is idempotent (natural-key upsert): still 5 rows, not 10.
        with Session(migrated_engine) as session, session.begin():
            enqueue(
                session,
                source_id=src_id,
                job_type=JobType.FETCH,
                dedupe_key="fetch:fred_macro_test:2",
                scheduled_at=NOW,
            )
        again = run_one_fetch(migrated_engine, fetcher=_fetcher(), raw_store=raw_store, clock=clock)
        assert isinstance(again, MacroFetchResult)
        with Session(migrated_engine) as session:
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(TimeSeriesPoint)
                    .where(TimeSeriesPoint.series_id == SERIES)
                )
                == 5
            )

        # get_macro_series (MCP): ordered ascending, value as string, missing -> null, unit set.
        server = build_mcp_server(migrated_engine)
        _blocks, out = asyncio.run(server.call_tool(TOOL_GET_MACRO_SERIES, {"series_id": SERIES}))
        assert out["found"] is True
        assert out["unit"] == "Index 1982-1984=100"
        assert out["count"] == 5
        values = [p["value"] for p in out["points"]]
        assert values == ["309.7", "310.5", "311.8", None, "312.4"]
        timestamps = [p["ts"] for p in out["points"]]
        assert timestamps == sorted(timestamps)  # ascending

        # A missing series returns found=false with a reason.
        _b, miss = asyncio.run(
            server.call_tool(TOOL_GET_MACRO_SERIES, {"series_id": "DOES_NOT_EXIST"})
        )
        assert miss["found"] is False
        assert "reason" in miss

        # Each call is audited (actor=mcp, provider:series target_ref).
        with Session(migrated_engine) as session:
            audits = session.scalars(
                select(AuditLog).where(AuditLog.tool_name == TOOL_GET_MACRO_SERIES)
            ).all()
            assert len(audits) >= 2
            assert audits[0].actor == Actor.MCP.value
            assert audits[0].action == MCP_TOOL_CALL
            assert any(a.target_ref == f"fred:{SERIES}" for a in audits)
    finally:
        if src_id is not None:
            _cleanup(migrated_engine, src_id)
