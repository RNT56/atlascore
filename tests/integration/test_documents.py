"""Integration: ``document`` upsert semantics (docs/schemas §2.4, R2) against Postgres 16.

Covers create / unchanged / last-write-wins update on a canonical URL (row stays 1, content
changes, enrichment columns preserved), and the null-URL best-effort dedupe.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session

from atlascore.core.ids import new_id
from atlascore.schemas.document import DocumentRecord
from atlascore.storage.documents import upsert_document
from atlascore.storage.postgres.models import Document, RawDocument, Source

pytestmark = pytest.mark.integration

NOW = datetime(2026, 6, 4, 12, 0, 0, tzinfo=UTC)


def _seed_source_and_raw(session: Session) -> tuple[Source, RawDocument]:
    source = Source(
        name="doc-upsert-test",
        category="news_rss",
        access_method="rss",
        policy_status="approved",
        enabled=True,
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
    return source, raw


def test_upsert_create_unchanged_update(migrated_engine: Engine) -> None:
    src_id = None
    try:
        with Session(migrated_engine) as session, session.begin():
            source, raw = _seed_source_and_raw(session)
            src_id = source.id
            rec = DocumentRecord(
                source_id=source.id,
                raw_document_id=raw.id,
                canonical_url="https://x.test/a",
                title="First",
                body_text="original body",
                content_hash="sha256:v1",
                parsed_at=NOW,
                parser_version="article@1.0.0",
            )
            r1 = upsert_document(session, rec, now=NOW)
            assert r1.outcome == "created"

            # Same content -> idempotent no-op.
            r2 = upsert_document(session, rec, now=NOW)
            assert r2.outcome == "unchanged"
            assert r2.document_id == r1.document_id

            # Simulate enrichment writing topics; then re-parse with a changed body.
            doc = session.get(Document, r1.document_id)
            assert doc is not None
            doc.topics = ["macro"]
            session.flush()

            changed = rec.model_copy(
                update={"body_text": "revised body", "content_hash": "sha256:v2"}
            )
            r3 = upsert_document(session, changed, now=NOW)
            assert r3.outcome == "updated"
            assert r3.document_id == r1.document_id  # same row (last-write-wins)

        with Session(migrated_engine) as session:
            count = session.scalar(
                select(func.count()).select_from(Document).where(Document.source_id == src_id)
            )
            assert count == 1  # never duplicated
            doc = session.scalar(select(Document).where(Document.source_id == src_id))
            assert doc is not None
            assert doc.body_text == "revised body"
            assert doc.content_hash == "sha256:v2"
            assert doc.topics == ["macro"]  # enrichment preserved across re-parse
    finally:
        if src_id is not None:
            with Session(migrated_engine) as session:
                session.execute(delete(Document).where(Document.source_id == src_id))
                session.execute(delete(RawDocument).where(RawDocument.source_id == src_id))
                session.execute(delete(Source).where(Source.id == src_id))
                session.commit()


def test_null_url_dedupe(migrated_engine: Engine) -> None:
    src_id = None
    try:
        with Session(migrated_engine) as session, session.begin():
            source, raw = _seed_source_and_raw(session)
            src_id = source.id
            base = DocumentRecord(
                source_id=source.id,
                raw_document_id=raw.id,
                canonical_url=None,
                body_text="a",
                content_hash="sha256:n1",
                parsed_at=NOW,
                parser_version="article@1.0.0",
            )
            assert upsert_document(session, base, now=NOW).outcome == "created"
            # Identical null-URL content -> unchanged (best-effort dedupe by content_hash).
            assert upsert_document(session, base, now=NOW).outcome == "unchanged"
            # Different content_hash -> a distinct null-URL document.
            other = base.model_copy(update={"body_text": "b", "content_hash": "sha256:n2"})
            assert upsert_document(session, other, now=NOW).outcome == "created"

        with Session(migrated_engine) as session:
            count = session.scalar(
                select(func.count()).select_from(Document).where(Document.source_id == src_id)
            )
            assert count == 2
    finally:
        if src_id is not None:
            with Session(migrated_engine) as session:
                session.execute(delete(Document).where(Document.source_id == src_id))
                session.execute(delete(RawDocument).where(RawDocument.source_id == src_id))
                session.execute(delete(Source).where(Source.id == src_id))
                session.commit()
