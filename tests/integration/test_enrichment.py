"""Integration: B11 enrichment + entity surface against Postgres 16 (entity/mention/embedding).

The B11 done-when: **entity mentions are queryable** via `get_entity_mentions`. This seeds a
canonical document, runs the enrichment pipeline (language → NER → topics → embeddings), and
asserts: the document gains topics/entity_ids; entity/mention/embedding rows are created; the
MCP `get_entity_mentions` tool resolves the entity by id / fuzzy name / identifier and returns
sanitized mentions with provenance; re-enrichment is idempotent (R13 embed skip, stable mentions);
and the `search_documents` topic/entity filters (deferred from B7) now work.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session

from atlascore.core.ids import new_id
from atlascore.mcp_server import TOOL_GET_ENTITY_MENTIONS, build_mcp_server
from atlascore.schemas.document import DocumentRecord
from atlascore.storage.documents import upsert_document
from atlascore.storage.entities import list_mentions, resolve_entity
from atlascore.storage.postgres.models import (
    Document,
    Embedding,
    Entity,
    Mention,
    RawDocument,
    Source,
)
from atlascore.storage.search import search_documents
from atlascore.worker.enrich import enrich_document_by_id

pytestmark = pytest.mark.integration

NOW = datetime(2026, 6, 6, 12, 0, 0, tzinfo=UTC)
SLUG = "enrich_it"
BODY = (
    "Apple Inc. (NASDAQ: AAPL) filed its quarterly earnings with the SEC. "
    "The Federal Reserve held interest rates steady amid inflation concerns. "
    "Analysts at Goldman Sachs discussed the CVE patch and a remote code execution vulnerability."
)


def _cleanup(session: Session) -> None:
    """Remove this test's rows (FK order: mention/embedding -> document -> raw -> source/entity)."""
    src_ids = list(session.scalars(select(Source.id).where(Source.config["slug"].astext == SLUG)))
    if src_ids:
        doc_ids = list(session.scalars(select(Document.id).where(Document.source_id.in_(src_ids))))
        if doc_ids:
            session.execute(delete(Mention).where(Mention.document_id.in_(doc_ids)))
            session.execute(delete(Embedding).where(Embedding.document_id.in_(doc_ids)))
            session.execute(delete(Document).where(Document.id.in_(doc_ids)))
        session.execute(delete(RawDocument).where(RawDocument.source_id.in_(src_ids)))
        session.execute(delete(Source).where(Source.id.in_(src_ids)))
    # Entities are global; drop the ones this test would have created.
    session.execute(
        delete(Entity).where(Entity.canonical_name.in_(["Apple Inc", "AAPL", "Federal Reserve"]))
    )


def _seed_document(session: Session) -> tuple[Source, Document]:
    source = Source(
        name="enrich-it-source",
        category="finance_filings",
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
    result = upsert_document(
        session,
        DocumentRecord(
            source_id=source.id,
            raw_document_id=raw.id,
            canonical_url="https://enrich.test/a",
            title="Apple earnings and a security patch",
            body_text=BODY,
            published_at=datetime(2026, 6, 5, 18, 0, tzinfo=UTC),
            content_hash="sha256:enrich-doc-1",
            parser_version="article@1.0.0",
        ),
        now=NOW,
    )
    doc = session.get(Document, result.document_id)
    assert doc is not None
    return source, doc


def test_enrich_creates_entities_mentions_embeddings(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)
        _, doc = _seed_document(session)
        doc_id = doc.id

    result = enrich_document_by_id(migrated_engine, doc_id)
    assert result.entities > 0
    assert result.mentions == result.entities  # one mention per resolved entity here
    assert result.embeddings >= 1  # body chunked + embedded

    with Session(migrated_engine) as session:
        doc = session.get(Document, doc_id)
        assert doc is not None
        # document enriched in place
        assert set(doc.topics) >= {"finance", "macroeconomics", "cybersecurity"}
        assert len(doc.entity_ids) == result.entities
        # entity/mention/embedding rows exist
        mentions = session.scalars(select(Mention).where(Mention.document_id == doc_id)).all()
        assert len(mentions) == result.mentions
        assert all(m.model_name == "rule-ner" for m in mentions)
        assert any(m.context_snippet and "Apple" in m.context_snippet for m in mentions)
        emb_count = session.scalar(
            select(func.count()).select_from(Embedding).where(Embedding.document_id == doc_id)
        )
        assert emb_count == result.embeddings

        # The ticker entity carries its identifier.
        aapl = session.scalar(select(Entity).where(Entity.canonical_name == "AAPL"))
        assert aapl is not None
        assert aapl.identifiers == {"ticker": "AAPL"}

    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)


def test_enrich_idempotent_reembed_skip(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)
        _, doc = _seed_document(session)
        doc_id = doc.id

    first = enrich_document_by_id(migrated_engine, doc_id)
    assert first.embeddings >= 1
    second = enrich_document_by_id(migrated_engine, doc_id)
    assert second.embeddings == 0  # R13: unchanged source_hash -> no-op skip
    assert second.mentions == first.mentions  # mentions stable (cleared + recreated)

    with Session(migrated_engine) as session:
        # No duplicate mentions accumulated across the two runs.
        total = session.scalar(
            select(func.count()).select_from(Mention).where(Mention.document_id == doc_id)
        )
        assert total == first.mentions

    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)


def test_get_entity_mentions_resolution(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)
        _, doc = _seed_document(session)
        doc_id = doc.id
    enrich_document_by_id(migrated_engine, doc_id)

    with Session(migrated_engine) as session:
        # by identifier (ticker)
        by_ticker = resolve_entity(session, identifier={"ticker": "AAPL"})
        assert by_ticker is not None and by_ticker.match_confidence == 0.95
        # by fuzzy name (trigram) — "Apple" ~ "Apple Inc"
        by_name = resolve_entity(session, name="Apple")
        assert by_name is not None
        # by exact id
        by_id = resolve_entity(session, entity_id=by_name.entity_id)
        assert by_id is not None and by_id.match_confidence == 1.0

        rows = list_mentions(session, by_name.entity_id, limit=50)
        assert len(rows) >= 1
        assert rows[0].canonical_url == "https://enrich.test/a"

    # End-to-end through the MCP tool (in-process call of the registered handler).
    # FastMCP call_tool returns (content_blocks, structured) — the structured dict is what
    # agents consume.
    server = build_mcp_server(migrated_engine)
    _blocks, structured = asyncio.run(
        server.call_tool(TOOL_GET_ENTITY_MENTIONS, {"name": "Federal Reserve"})
    )
    assert structured["found"] is True
    assert structured["entity"]["canonical_name"] == "Federal Reserve"
    assert structured["count"] >= 1
    assert structured["mentions"][0]["provenance"]["source"] == "enrich-it-source"

    # Unknown entity -> found=false with a reason (never fabricated).
    _miss_blocks, miss_struct = asyncio.run(
        server.call_tool(TOOL_GET_ENTITY_MENTIONS, {"name": "Nonexistent Megacorp"})
    )
    assert miss_struct["found"] is False

    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)


def test_search_topic_and_entity_filters(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)
        _, doc = _seed_document(session)
        doc_id = doc.id
    enrich_document_by_id(migrated_engine, doc_id)

    with Session(migrated_engine) as session:
        doc = session.get(Document, doc_id)
        assert doc is not None
        entity_id = doc.entity_ids[0]

        # topic filter narrows to enriched topics
        hit_topic = search_documents(session, "Apple", topics=["finance"])
        assert any(h.document_id == doc_id for h in hit_topic)
        assert search_documents(session, "Apple", topics=["sports"]) == []

        # entity_ids filter
        hit_entity = search_documents(session, "Apple", entity_ids=[entity_id])
        assert any(h.document_id == doc_id for h in hit_entity)
        assert search_documents(session, "Apple", entity_ids=[str(new_id())]) == []

    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)
