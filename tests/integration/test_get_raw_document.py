"""Integration: the `get_raw_document` MCP tool against Postgres 16 (B15 polish — the 7th tool).

Seeds a source + raw blob (containing active HTML) + a document, then drives the MCP tool: the
default returns sanitized text (no script), `include_raw_html=true` returns the verbatim raw HTML
wrapped with the untrusted-evidence notice and writes a distinct `mcp_raw_html_returned` audit row.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session

from atlascore.core.settings import get_settings
from atlascore.schemas.document import DocumentRecord
from atlascore.storage.documents import upsert_document
from atlascore.storage.postgres.models import AuditLog, Document, RawDocument, Source
from atlascore.storage.rawstore import RawStore

pytestmark = pytest.mark.integration

NOW = datetime(2026, 6, 6, 12, 0, tzinfo=UTC)
SLUG = "rawdoc_it"
RAW_HTML = (
    b"<html><body><p>Quarterly results.</p><script>steal(document.cookie)</script></body></html>"
)


@pytest.fixture
def raw_store_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    """Point the process raw store at a tmp dir so the tool's RawStore.from_settings() reads it."""
    monkeypatch.setenv("RAW_STORE_DIR", str(tmp_path / "raw"))
    get_settings.cache_clear()
    yield tmp_path / "raw"
    get_settings.cache_clear()


def _cleanup(session: Session) -> None:
    src_ids = list(session.scalars(select(Source.id).where(Source.config["slug"].astext == SLUG)))
    if src_ids:
        session.execute(delete(Document).where(Document.source_id.in_(src_ids)))
        session.execute(delete(RawDocument).where(RawDocument.source_id.in_(src_ids)))
        session.execute(delete(Source).where(Source.id.in_(src_ids)))
    session.execute(delete(AuditLog).where(AuditLog.tool_name == "get_raw_document"))


def test_get_raw_document_sanitized_default_and_raw_flag(
    migrated_engine: Engine, raw_store_tmp: Path
) -> None:
    from atlascore.mcp_server import TOOL_GET_RAW_DOCUMENT, build_mcp_server

    store = RawStore(raw_store_tmp)
    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)
        source = Source(
            name="rawdoc-it-source",
            category="news_rss",
            access_method="rss",
            policy_status="approved",
            enabled=True,
            config={"slug": SLUG},
            schema_version=1,
        )
        session.add(source)
        session.flush()
        blob = store.put(RAW_HTML)  # write the raw blob to the settings-backed store
        raw = RawDocument(
            source_id=source.id,
            content_hash=blob.content_hash,
            path=blob.relpath,
            mime_type="text/html",
            size_bytes=len(RAW_HTML),
            http_status=200,
            schema_version=1,
        )
        session.add(raw)
        session.flush()
        result = upsert_document(
            session,
            DocumentRecord(
                source_id=source.id,
                raw_document_id=raw.id,
                canonical_url="https://rawdoc.test/a",
                title="Results",
                body_text="Quarterly results.",
                content_hash="sha256:rawdoc",
                parser_version="article@1.0.0",
            ),
            now=NOW,
        )
        doc_id = result.document_id

    server = build_mcp_server(migrated_engine)

    # Default: sanitized text — the script is gone.
    _b, default = asyncio.run(server.call_tool(TOOL_GET_RAW_DOCUMENT, {"document_id": str(doc_id)}))
    assert default["content_kind"] == "sanitized_text"
    assert "Quarterly results." in default["content"]
    assert "steal" not in default["content"] and "<script" not in default["content"]
    assert "untrusted_evidence_notice" not in default
    assert default["provenance"]["canonical_url"] == "https://rawdoc.test/a"

    # include_raw_html=true: verbatim raw HTML, wrapped + separately audited.
    _b2, raw_out = asyncio.run(
        server.call_tool(
            TOOL_GET_RAW_DOCUMENT, {"document_id": str(doc_id), "include_raw_html": True}
        )
    )
    assert raw_out["content_kind"] == "raw_html"
    assert "<script>steal(document.cookie)</script>" in raw_out["content"]
    assert raw_out["untrusted_evidence_notice"].startswith("This is RAW")

    with Session(migrated_engine) as session:
        raw_html_audits = session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.action == "mcp_raw_html_returned",
                AuditLog.tool_name == "get_raw_document",
                AuditLog.target_ref == str(doc_id),
            )
        )
        assert raw_html_audits == 1  # the raw-HTML disclosure is independently auditable

    # Unknown id → clean not-found (never a crash).
    _b3, miss = asyncio.run(server.call_tool(TOOL_GET_RAW_DOCUMENT, {"document_id": "not-a-uuid"}))
    assert miss["found"] is False

    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)
