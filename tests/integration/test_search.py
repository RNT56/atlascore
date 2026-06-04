"""Integration: the B6 full-text search query path against Postgres 16.

Seeds a handful of canonical ``document`` rows and exercises ``search_documents``: text match
+ ranking, the source/since/until filters, the result cap, snippet + provenance, and — the
load-bearing regression — that the query actually uses the ``ix_document_fts`` GIN index
(asserted via ``EXPLAIN`` with seqscan disabled, which only picks the index when the query's
tsvector expression is byte-identical to the index expression).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, delete, text
from sqlalchemy.orm import Session

from atlascore.core.ids import new_id
from atlascore.schemas.document import DocumentRecord
from atlascore.storage.documents import upsert_document
from atlascore.storage.postgres.models import Document, RawDocument, Source
from atlascore.storage.search import MAX_LIMIT, search_documents
from atlascore.storage.search.query import _SEARCH_SQL

pytestmark = pytest.mark.integration

NOW = datetime(2026, 6, 6, 12, 0, 0, tzinfo=UTC)
SLUG = "search_it"


def _seed(session: Session) -> Source:
    source = Source(
        name="search-it-source",
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
        # url, title, body, published_at
        (
            "https://s.test/a",
            "Rate decision",
            "The committee decided to hold interest rates steady amid inflation concerns.",
            datetime(2026, 6, 3, 18, 0, tzinfo=UTC),
        ),
        (
            "https://s.test/b",
            "Jobs report",
            "Employment grew last month with strong job gains across many sectors.",
            datetime(2026, 6, 1, 12, 0, tzinfo=UTC),
        ),
        (
            "https://s.test/c",
            "Rates outlook",
            "Rates, rates, rates dominate the rates outlook for the year ahead.",
            datetime(2026, 6, 5, 9, 0, tzinfo=UTC),
        ),
    ]
    for i, (url, title, body, pub) in enumerate(docs):
        upsert_document(
            session,
            DocumentRecord(
                source_id=source.id,
                raw_document_id=raw.id,
                canonical_url=url,
                title=title,
                body_text=body,
                published_at=pub,
                content_hash=f"sha256:doc{i}",
                parser_version="article@1.0.0",
            ),
            now=NOW,
        )
    return source


def _cleanup(engine: Engine, src_id: object) -> None:
    with Session(engine) as session:
        session.execute(delete(Document).where(Document.source_id == src_id))
        session.execute(delete(RawDocument).where(RawDocument.source_id == src_id))
        session.execute(delete(Source).where(Source.id == src_id))
        session.commit()


def test_search_query_path(migrated_engine: Engine) -> None:
    src_id = None
    try:
        with Session(migrated_engine) as session, session.begin():
            src_id = _seed(session).id

        with Session(migrated_engine) as session:
            # Text match: "rates" hits the rate-decision + outlook docs, not the jobs doc.
            hits = search_documents(session, "rates", source=SLUG)
            titles = {h.title for h in hits}
            assert titles == {"Rate decision", "Rates outlook"}

            # Ranked descending; the 4-occurrence doc outranks the 1-occurrence doc.
            assert all(hits[i].rank >= hits[i + 1].rank for i in range(len(hits) - 1))
            assert hits[0].title == "Rates outlook"

            # Provenance + sanitized snippet (matched term wrapped in guillemets).
            top = hits[0]
            assert top.source_slug == SLUG
            assert top.source_name == "search-it-source"
            assert top.canonical_url == "https://s.test/c"
            assert "«rates»" in top.snippet

            # Source filter: a different slug matches nothing.
            assert search_documents(session, "rates", source="other") == []

            # Date filters: since is inclusive, until is exclusive.
            since = search_documents(
                session, "rates", source=SLUG, since=datetime(2026, 6, 4, tzinfo=UTC)
            )
            assert {h.title for h in since} == {"Rates outlook"}  # 06-05 kept, 06-03 dropped
            until = search_documents(
                session, "rates", source=SLUG, until=datetime(2026, 6, 4, tzinfo=UTC)
            )
            assert {h.title for h in until} == {"Rate decision"}  # 06-03 kept, 06-05 dropped

            # Result cap.
            assert len(search_documents(session, "rates", source=SLUG, limit=1)) == 1

            # Empty / whitespace query never hits the DB-match.
            assert search_documents(session, "   ") == []

            # Robust to stray FTS operators (websearch_to_tsquery does not raise).
            assert len(search_documents(session, "rates & | !(", source=SLUG)) == 2

        # The query uses ix_document_fts: with seqscan penalized the planner can only choose
        # the GIN index if the tsvector expression matches the index expression exactly.
        with Session(migrated_engine) as session, session.begin():
            session.execute(text("SET LOCAL enable_seqscan = off"))
            plan = session.execute(
                text("EXPLAIN (COSTS OFF) " + str(_SEARCH_SQL)),
                {"q": "rates", "source": None, "since": None, "until": None, "limit": MAX_LIMIT},
            ).all()
            assert "ix_document_fts" in "\n".join(row[0] for row in plan)
    finally:
        if src_id is not None:
            _cleanup(migrated_engine, src_id)
