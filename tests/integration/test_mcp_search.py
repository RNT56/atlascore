"""Integration: the B7 MCP ``search_documents`` tool + ``audit_log`` against Postgres 16.

Drives the FastMCP tool through ``call_tool`` (async, run via ``asyncio.run``): the agent gets
ranked, sanitized hits with full provenance over the in-process server; every call writes a
sanitized ``audit_log`` row; result caps and input validation hold; and a secret embedded in the
query is redacted before it reaches ``audit_log.detail`` (the B7 audit-sanitization regression).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session

from atlascore.core.ids import new_id
from atlascore.mcp_server import SEARCH_LIMIT_MAX, TOOL_SEARCH_DOCUMENTS, build_mcp_server
from atlascore.schemas.audit_log import MCP_TOOL_CALL, Actor
from atlascore.schemas.document import DocumentRecord
from atlascore.storage.documents import upsert_document
from atlascore.storage.postgres.models import AuditLog, Document, RawDocument, Source

pytestmark = pytest.mark.integration

NOW = datetime(2026, 6, 6, 12, 0, 0, tzinfo=UTC)
SLUG = "mcp_it"


def _seed(session: Session) -> Source:
    source = Source(
        name="mcp-it-source",
        category="news_rss",
        access_method="rss",
        policy_status="approved",
        enabled=True,
        config={"slug": SLUG},
        schema_version=1,
    )
    session.add(source)
    session.flush()
    raw = RawDocument(
        source_id=source.id,
        content_hash=f"sha256:{new_id().hex}",
        path="ab/cd/ef.gz",
        schema_version=1,
    )
    session.add(raw)
    session.flush()
    docs = [
        ("https://m.test/a", "Rate decision", "The committee held interest rates steady.", "en"),
        ("https://m.test/b", "Rates outlook", "Rates, rates, rates dominate the outlook.", "en"),
        ("https://m.test/c", "Jobs report", "Employment grew with strong job gains.", "en"),
    ]
    for i, (url, title, body, lang) in enumerate(docs):
        upsert_document(
            session,
            DocumentRecord(
                source_id=source.id,
                raw_document_id=raw.id,
                canonical_url=url,
                title=title,
                body_text=body,
                language=lang,
                published_at=datetime(2026, 6, 3 + i, 12, 0, tzinfo=UTC),
                content_hash=f"sha256:doc{i}",
                parser_version="article@1.0.0",
            ),
            now=NOW,
        )
    return source


def _cleanup(engine: Engine, src_id: UUID) -> None:
    with Session(engine) as session:
        session.execute(delete(AuditLog).where(AuditLog.tool_name == TOOL_SEARCH_DOCUMENTS))
        session.execute(delete(Document).where(Document.source_id == src_id))
        session.execute(delete(RawDocument).where(RawDocument.source_id == src_id))
        session.execute(delete(Source).where(Source.id == src_id))
        session.commit()


def _call(server: Any, args: dict[str, Any]) -> dict[str, Any]:
    """Invoke a tool and return its structured output (call_tool -> (blocks, structured))."""
    _blocks, structured = asyncio.run(server.call_tool(TOOL_SEARCH_DOCUMENTS, args))
    return structured


def test_mcp_search_documents_and_audit(migrated_engine: Engine) -> None:
    src_id = None
    try:
        with Session(migrated_engine) as session, session.begin():
            src_id = _seed(session).id
        server = build_mcp_server(migrated_engine)

        # The tool is registered with the expected input contract.
        tools = asyncio.run(server.list_tools())
        tool = next(t for t in tools if t.name == TOOL_SEARCH_DOCUMENTS)
        assert tool.inputSchema["required"] == ["query"]
        assert set(tool.inputSchema["properties"]) >= {
            "query",
            "source",
            "published_after",
            "published_before",
            "limit",
        }

        # Ranked hits with sanitized snippet + full provenance; the jobs doc is excluded.
        out = _call(server, {"query": "rates", "source": SLUG})
        assert out["count"] == 2
        titles = {r["title"] for r in out["results"]}
        assert titles == {"Rate decision", "Rates outlook"}
        ranks = [r["rank"] for r in out["results"]]
        assert ranks == sorted(ranks, reverse=True)
        top = out["results"][0]
        assert top["title"] == "Rates outlook"  # 3 occurrences outranks 1
        assert "«rates»" in top["snippet"]
        assert top["language"] == "en"
        assert top["topics"] == []  # enrichment not run yet (B11)
        prov = top["provenance"]
        assert prov["source_id"] == str(src_id)
        assert prov["source"] == "mcp-it-source"
        assert prov["source_slug"] == SLUG
        assert prov["canonical_url"] in {"https://m.test/a", "https://m.test/b"}
        assert prov["published_at"] is not None
        assert prov["parser_version"] == "article@1.0.0"

        # Date filter: published_after is inclusive — keeps the 06-04 outlook, drops the
        # 06-03 decision (docs are published 06-03, 06-04, 06-05 for i=0,1,2).
        after = _call(
            server, {"query": "rates", "source": SLUG, "published_after": "2026-06-04T00:00:00"}
        )
        assert {r["title"] for r in after["results"]} == {"Rates outlook"}

        # Every call is audited (actor=mcp, routine) with sanitized params.
        with Session(migrated_engine) as session:
            audits = session.scalars(
                select(AuditLog).where(AuditLog.tool_name == TOOL_SEARCH_DOCUMENTS)
            ).all()
            assert len(audits) >= 2
            a = audits[0]
            assert a.actor == Actor.MCP.value
            assert a.action == MCP_TOOL_CALL
            assert a.retention_class == "routine"
            assert a.source_id is None  # search is not scoped to one source row
            assert a.detail["query"] == "rates"

        # Security regression: a secret in the query is redacted before it lands in audit.detail.
        _call(server, {"query": "rates apikey=SUPERSECRET token=abc123"})
        with Session(migrated_engine) as session:
            secret_audits = session.scalars(
                select(AuditLog).where(AuditLog.tool_name == TOOL_SEARCH_DOCUMENTS)
            ).all()
            stored = [a.detail.get("query", "") for a in secret_audits]
            assert any("[REDACTED]" in q for q in stored)
            assert all("SUPERSECRET" not in q for q in stored)
            assert all("abc123" not in q for q in stored)

        # Caps + validation: limit is bounded to SEARCH_LIMIT_MAX; empty query rejected.
        capped = _call(server, {"query": "rates", "source": SLUG, "limit": 1})
        assert capped["count"] == 1
        with pytest.raises(ToolError):
            _call(server, {"query": "rates", "limit": SEARCH_LIMIT_MAX + 1})
        with pytest.raises(ToolError):
            _call(server, {"query": ""})
    finally:
        if src_id is not None:
            _cleanup(migrated_engine, src_id)
