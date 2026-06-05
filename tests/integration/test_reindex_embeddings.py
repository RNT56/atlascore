"""Integration: B21 atlas reindex-embeddings backfill/reindex on Postgres 16 (R8)."""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session

from atlascore.cli.reindex import _run
from atlascore.core.ids import new_id
from atlascore.enrichment.embeddings.hashing import HashingEmbeddingBackend
from atlascore.schemas.document import DocumentRecord
from atlascore.storage.documents import upsert_document
from atlascore.storage.embeddings import EMBEDDING_DIM, upsert_embeddings
from atlascore.storage.postgres.models import Document, Embedding, RawDocument, Source

pytestmark = pytest.mark.integration

SLUG = "reindex_it"


class _StubBackend:
    """A same-dim backend with a distinct (model_name, version) so coverage can flip."""

    model_name = "stub-embed"
    model_version = "v2"
    dim = EMBEDDING_DIM

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.01 * (i + 1)] * self.dim for i, _ in enumerate(texts)]


def _cleanup(session: Session) -> None:
    ids = list(
        session.scalars(select(Source.id).where(Source.config["slug"].astext.like(f"{SLUG}%")))
    )
    if ids:
        doc_ids = list(session.scalars(select(Document.id).where(Document.source_id.in_(ids))))
        if doc_ids:
            session.execute(delete(Embedding).where(Embedding.document_id.in_(doc_ids)))
        session.execute(delete(Document).where(Document.source_id.in_(ids)))
        session.execute(delete(RawDocument).where(RawDocument.source_id.in_(ids)))
        session.execute(delete(Source).where(Source.id.in_(ids)))


def _seed(session: Session) -> None:
    from datetime import UTC, datetime

    now = datetime(2026, 6, 6, tzinfo=UTC)
    source = Source(
        name=f"{SLUG}-src",
        category="news_rss",
        access_method="rss",
        policy_status="approved",
        enabled=True,
        config={"slug": SLUG},
        schema_version=1,
    )
    session.add(source)
    session.flush()
    for i in range(2):
        raw = RawDocument(
            source_id=source.id,
            content_hash=f"sha256:{new_id().hex}",
            path=f"a/{i}.gz",
            schema_version=1,
        )
        session.add(raw)
        session.flush()
        result = upsert_document(
            session,
            DocumentRecord(
                source_id=source.id,
                raw_document_id=raw.id,
                canonical_url=f"https://{SLUG}.test/{i}",
                title=f"Doc {i}",
                body_text=f"Body text number {i} with enough content to chunk.",
                published_at=now,
                content_hash=f"sha256:{SLUG}-{i}",
                parser_version="article@1.0.0",
            ),
            now=now,
        )
        # Initial embeddings via the hashing backend (the existing coverage).
        upsert_embeddings(
            session,
            document_id=result.document_id,
            source_hash=f"sha256:{SLUG}-{i}",
            body_text=f"Body text number {i} with enough content to chunk.",
            backend=HashingEmbeddingBackend(dim=EMBEDDING_DIM),
        )


def _count(session: Session, model_name: str) -> int:
    return (
        session.scalar(
            select(func.count()).select_from(Embedding).where(Embedding.model_name == model_name)
        )
        or 0
    )


def test_reindex_flips_coverage_and_is_idempotent(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)
        _seed(session)
    with Session(migrated_engine) as session:
        assert _count(session, "hashing") >= 2  # initial hashing coverage
        assert _count(session, "stub-embed") == 0

    # Reindex with the stub backend → new (model, version) coverage appears.
    with Session(migrated_engine) as session:
        written = _run(session, _StubBackend(), batch=1)
    assert written >= 2
    with Session(migrated_engine) as session:
        assert _count(session, "stub-embed") >= 2

    # Idempotent: a second reindex re-embeds nothing (unchanged source_hash, R13).
    with Session(migrated_engine) as session:
        again = _run(session, _StubBackend(), batch=1)
    assert again == 0

    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)
