"""Integration: B12 summaries pipeline + `get_document_summary` against Postgres 16.

The B12 done-when: a cached, cited summary via MCP; no re-summarization of an identical tuple. This
seeds documents and exercises: compute-on-miss → store, cache HIT (no new row, no recompute),
single vs multi scope, the `LLM_BACKEND=none` cache-only null+reason, `allow_compute=false`, the
content-change cache bust, and the `get_document_summary` MCP tool end to end.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session

from atlascore.core.ids import new_id
from atlascore.core.settings import get_settings
from atlascore.enrichment.summaries import ExtractiveSummarizer, get_summary_backend
from atlascore.enrichment.summaries.pipeline import summarize_documents
from atlascore.mcp_server import TOOL_GET_DOCUMENT_SUMMARY, build_mcp_server
from atlascore.schemas.document import DocumentRecord
from atlascore.storage.documents import upsert_document
from atlascore.storage.postgres.models import Document, RawDocument, Source, Summary

pytestmark = pytest.mark.integration

NOW = datetime(2026, 6, 6, 12, 0, 0, tzinfo=UTC)
SLUG = "summary_it"


@pytest.fixture
def llm_local(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Force LLM_BACKEND=local for the MCP tool path (the deterministic extractive backend)."""
    monkeypatch.setenv("LLM_BACKEND", "local")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _cleanup(session: Session) -> None:
    src_ids = list(session.scalars(select(Source.id).where(Source.config["slug"].astext == SLUG)))
    if not src_ids:
        return
    doc_ids = list(session.scalars(select(Document.id).where(Document.source_id.in_(src_ids))))
    if doc_ids:
        session.execute(
            delete(Summary).where(
                Summary.document_id.in_(doc_ids)
                | Summary.member_doc_ids.op("&&")([str(d) for d in doc_ids])
            )
        )
        session.execute(delete(Document).where(Document.id.in_(doc_ids)))
    session.execute(delete(RawDocument).where(RawDocument.source_id.in_(src_ids)))
    session.execute(delete(Source).where(Source.id.in_(src_ids)))


def _seed(session: Session, n: int = 2) -> list[Document]:
    source = Source(
        name="summary-it-source",
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
    bodies = [
        (
            "CPI report",
            "Consumer prices rose 0.2% in May. Core inflation held steady.",
            "sha256:s1",
        ),
        (
            "Fed decision",
            "The Federal Reserve held interest rates steady. Markets rallied.",
            "sha256:s2",
        ),
    ]
    docs: list[Document] = []
    for i in range(n):
        title, body, ch = bodies[i]
        result = upsert_document(
            session,
            DocumentRecord(
                source_id=source.id,
                raw_document_id=raw.id,
                canonical_url=f"https://summary.test/{i}",
                title=title,
                body_text=body,
                published_at=datetime(2026, 6, 5, 18, i, tzinfo=UTC),
                content_hash=ch,
                parser_version="article@1.0.0",
            ),
            now=NOW,
        )
        doc = session.get(Document, result.document_id)
        assert doc is not None
        docs.append(doc)
    return docs


def test_compute_then_cache_hit_single_and_multi(migrated_engine: Engine) -> None:
    backend = ExtractiveSummarizer()
    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)
        docs = _seed(session, n=2)
        ids = [d.id for d in docs]

    # First call: compute + store (cache miss).
    with Session(migrated_engine) as session, session.begin():
        first = summarize_documents(session, ids, backend=backend)
    assert first.found and first.cache == "miss"
    assert first.summary is not None and first.summary.scope == "multi"
    assert set(first.summary.citations.keys()) == {str(i) for i in ids}
    assert "Consumer prices rose" in first.summary.summary_text

    # Second call (identical tuple): cache HIT — no recompute, no new row.
    with Session(migrated_engine) as session, session.begin():
        second = summarize_documents(session, ids, backend=backend)
    assert second.found and second.cache == "hit"
    assert second.summary is not None
    assert second.summary.summary_id == first.summary.summary_id  # same cached row

    with Session(migrated_engine) as session:
        n_multi = session.scalar(
            select(func.count()).select_from(Summary).where(Summary.scope == "multi")
        )
        assert n_multi == 1  # never re-summarized the identical tuple

    # Single-doc scope.
    with Session(migrated_engine) as session, session.begin():
        single = summarize_documents(session, [ids[0]], backend=backend)
    assert single.summary is not None and single.summary.scope == "single"
    assert single.summary.member_doc_ids == [str(ids[0])]

    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)


def test_cache_only_and_compute_disabled(migrated_engine: Engine) -> None:
    backend = ExtractiveSummarizer()
    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)
        docs = _seed(session, n=1)
        ids = [d.id for d in docs]

    # LLM_BACKEND=none (backend None) + nothing cached -> null + reason (no fabrication).
    with Session(migrated_engine) as session, session.begin():
        miss = summarize_documents(session, ids, backend=None)
    assert not miss.found and miss.summary is None
    assert "no cached summary" in (miss.reason or "")

    # allow_compute=false with a backend, still nothing cached -> null + reason (no compute).
    with Session(migrated_engine) as session, session.begin():
        no_compute = summarize_documents(session, ids, backend=backend, allow_compute=False)
    assert not no_compute.found and no_compute.summary is None

    # Populate the cache, then cache-only (backend None) returns the stored summary.
    with Session(migrated_engine) as session, session.begin():
        summarize_documents(session, ids, backend=backend)
    with Session(migrated_engine) as session, session.begin():
        hit = summarize_documents(session, ids, backend=None)
    assert hit.found and hit.cache == "hit" and hit.summary is not None

    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)


def test_content_change_busts_cache(migrated_engine: Engine) -> None:
    backend = ExtractiveSummarizer()
    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)
        docs = _seed(session, n=1)
        doc_id = docs[0].id

    with Session(migrated_engine) as session, session.begin():
        summarize_documents(session, [doc_id], backend=backend)
    # Re-parse changes the document's parsed content_hash -> new source_hash -> cache miss.
    with Session(migrated_engine) as session, session.begin():
        doc = session.get(Document, doc_id)
        assert doc is not None
        doc.content_hash = "sha256:changed"
        doc.body_text = "A completely different body after re-parse."
    with Session(migrated_engine) as session, session.begin():
        again = summarize_documents(session, [doc_id], backend=backend)
    assert again.found and again.cache == "miss"  # recomputed for the new content

    with Session(migrated_engine) as session:
        # Two distinct cached summaries now exist for this doc (old + new source_hash).
        n = session.scalar(
            select(func.count()).select_from(Summary).where(Summary.document_id == doc_id)
        )
        assert n == 2

    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)


def test_get_document_summary_mcp_tool(migrated_engine: Engine, llm_local: None) -> None:
    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)
        docs = _seed(session, n=2)
        ids = [str(d.id) for d in docs]

    # Backend resolves to the extractive summarizer under LLM_BACKEND=local.
    assert isinstance(get_summary_backend(), ExtractiveSummarizer)

    server = build_mcp_server(migrated_engine)
    # First call computes + stores (cache=miss); cited to internal doc ids.
    _b1, first = asyncio.run(server.call_tool(TOOL_GET_DOCUMENT_SUMMARY, {"document_ids": ids}))
    assert first["cache"] == "miss"
    assert first["scope"] == "multi"
    assert set(first["citations"].keys()) == set(ids)
    assert first["provenance"]["member_sources"][0]["source"] == "summary-it-source"

    # Second call hits the cache.
    _b2, second = asyncio.run(server.call_tool(TOOL_GET_DOCUMENT_SUMMARY, {"document_ids": ids}))
    assert second["cache"] == "hit"
    assert second["summary_id"] == first["summary_id"]

    # Invalid id -> clean null + reason (never a crash).
    _b3, bad = asyncio.run(
        server.call_tool(TOOL_GET_DOCUMENT_SUMMARY, {"document_ids": ["not-a-uuid"]})
    )
    assert bad["summary_text"] is None and "valid UUID" in bad["reason"]

    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)
