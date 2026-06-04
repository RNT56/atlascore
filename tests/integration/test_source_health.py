"""Integration: B8 freshness/health — `source_health` compute, `get_source_health` MCP, digest.

Seeds a stale source, a fresh source, and a repeatedly-failing source, runs the freshness
computation, and asserts the classified statuses; then drives `get_source_health` (audited,
secret-free) and the `atlas digest` CLI so a stale source provably surfaces — the B8 done-when.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from atlascore.cli.main import app
from atlascore.mcp_server import TOOL_GET_SOURCE_HEALTH, build_mcp_server
from atlascore.schemas.audit_log import MCP_TOOL_CALL, Actor
from atlascore.storage.postgres.models import (
    AuditLog,
    CrawlJob,
    Document,
    RawDocument,
    Source,
    SourceHealth,
)
from atlascore.storage.source_health import list_source_health
from atlascore.worker.freshness import refresh_source_health

pytestmark = pytest.mark.integration

NOW = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
FRESHNESS = {"target": "24h", "warn_after": "36h", "error_after": "72h"}
runner = CliRunner()


def _source(session: Session, name: str, slug: str) -> tuple[Source, RawDocument]:
    src = Source(
        name=name,
        category="news_rss",
        access_method="rss",
        policy_status="approved",
        enabled=True,
        base_url="https://feed.example.test/x",
        config={"slug": slug},
        freshness=FRESHNESS,
        schema_version=1,
    )
    session.add(src)
    session.flush()
    raw = RawDocument(
        source_id=src.id, content_hash=f"sha256:{slug}", path=f"a/{slug}.gz", schema_version=1
    )
    session.add(raw)
    session.flush()
    return src, raw


def _doc(session: Session, src: Source, raw: RawDocument, *, published_at: datetime) -> None:
    session.add(
        Document(
            source_id=src.id,
            raw_document_id=raw.id,
            canonical_url=f"https://x.test/{src.id}",
            body_text="body",
            published_at=published_at,
            ingested_at=published_at,
            content_hash=f"sha256:doc-{src.id}",
            parser_version="article@1.0.0",
            schema_version=1,
        )
    )


def _fetch_job(session: Session, src: Source, *, status: str, at: datetime, key: str) -> None:
    session.add(
        CrawlJob(
            source_id=src.id,
            job_type="fetch",
            status=status,
            dedupe_key=key,
            scheduled_at=at,
            claimed_at=at,
            finished_at=at,
            attempt=5 if status == "dead" else 1,
            max_attempts=5,
        )
    )


def _cleanup(engine: Engine, names: list[str]) -> None:
    with Session(engine) as session:
        ids = list(session.scalars(select(Source.id).where(Source.name.in_(names))))
        session.execute(delete(AuditLog).where(AuditLog.tool_name == TOOL_GET_SOURCE_HEALTH))
        for model in (SourceHealth, Document, RawDocument, CrawlJob):
            session.execute(delete(model).where(model.source_id.in_(ids)))
        session.execute(delete(Source).where(Source.id.in_(ids)))
        session.commit()


def test_refresh_classifies_and_get_source_health(migrated_engine: Engine) -> None:
    names = ["B8 Stale", "B8 Fresh", "B8 Failing"]
    try:
        with Session(migrated_engine) as session, session.begin():
            stale, sraw = _source(session, "B8 Stale", "b8_stale")
            _doc(session, stale, sraw, published_at=NOW - timedelta(days=10))
            _fetch_job(session, stale, status="done", at=NOW - timedelta(days=10), key="b8s")

            fresh, fraw = _source(session, "B8 Fresh", "b8_fresh")
            _doc(session, fresh, fraw, published_at=NOW - timedelta(hours=1))
            _fetch_job(session, fresh, status="done", at=NOW - timedelta(hours=1), key="b8f")

            failing, _ = _source(session, "B8 Failing", "b8_fail")
            for i in range(3):
                _fetch_job(
                    session, failing, status="dead", at=NOW - timedelta(hours=5 - i), key=f"b8x{i}"
                )

        with Session(migrated_engine) as session, session.begin():
            refresh_source_health(session, now=NOW)

        with Session(migrated_engine) as session:
            by_slug = {r.source_slug: r for r in list_source_health(session)}
            assert by_slug["b8_stale"].status == "stale"
            assert by_slug["b8_fresh"].status == "ok"
            assert by_slug["b8_fail"].status == "cooling_down"
            assert by_slug["b8_fail"].consecutive_failures == 3
            assert by_slug["b8_stale"].freshness_lag == timedelta(days=10)

        # get_source_health (MCP): filter to problems, audited, secret-free.
        server = build_mcp_server(migrated_engine)
        _blocks, out = asyncio.run(
            server.call_tool(TOOL_GET_SOURCE_HEALTH, {"status": ["stale", "cooling_down"]})
        )
        statuses = {s["source_slug"]: s["status"] for s in out["sources"]}
        assert statuses.get("b8_stale") == "stale"
        assert statuses.get("b8_fail") == "cooling_down"
        assert "b8_fresh" not in statuses
        secret_keys = {"base_url", "credential_ref", "config"}
        assert all(secret_keys.isdisjoint(s) for s in out["sources"])  # no secrets/hostnames
        stale_row = next(s for s in out["sources"] if s["source_slug"] == "b8_stale")
        assert stale_row["freshness_lag_seconds"] == int(timedelta(days=10).total_seconds())

        # A single-slug query returns the fresh source as ok.
        _b, one = asyncio.run(server.call_tool(TOOL_GET_SOURCE_HEALTH, {"sources": ["b8_fresh"]}))
        assert [(s["source_slug"], s["status"]) for s in one["sources"]] == [("b8_fresh", "ok")]

        # The call is audited (actor=mcp, sanitized detail).
        with Session(migrated_engine) as session:
            audits = session.scalars(
                select(AuditLog).where(AuditLog.tool_name == TOOL_GET_SOURCE_HEALTH)
            ).all()
            assert len(audits) >= 2
            assert audits[0].actor == Actor.MCP.value
            assert audits[0].action == MCP_TOOL_CALL
    finally:
        _cleanup(migrated_engine, names)


def test_digest_cli_surfaces_a_stale_source(migrated_engine: Engine) -> None:
    """`atlas digest` recomputes health (real clock) and lists a stale source. The doc is dated
    far in the past so it is stale regardless of the wall clock."""
    names = ["B8 CLI Stale"]
    ancient = datetime(2000, 1, 1, tzinfo=UTC)
    try:
        with Session(migrated_engine) as session, session.begin():
            src, raw = _source(session, "B8 CLI Stale", "b8_cli_stale")
            _doc(session, src, raw, published_at=ancient)
            _fetch_job(session, src, status="done", at=ancient, key="b8cli")

        result = runner.invoke(app, ["digest"])
        assert result.exit_code == 0
        assert "b8_cli_stale" in result.output
        assert "stale" in result.output
    finally:
        _cleanup(migrated_engine, names)
