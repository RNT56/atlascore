"""Integration: cloud-embedding spend metering + the §20 cap on Postgres 16.

Mirrors the summary spend-cap contract (`test_llm_spend_ledger.py`) for the embedding path: a paid
(cloud) embedding backend records one `llm_spend` row per embed under the monthly cap, and is
SKIPPED (no vectors written, no new spend) once month-to-date spend reaches the cap. The shared
`month_to_date_spend` sums summary + embedding rows, so the two paths share one monthly budget.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session

from atlascore.core.ids import new_id
from atlascore.schemas.document import DocumentRecord
from atlascore.storage.documents import upsert_document
from atlascore.storage.embeddings import upsert_embeddings
from atlascore.storage.llm_spend import record_spend
from atlascore.storage.postgres.models import Document, Embedding, LlmSpend, RawDocument, Source
from atlascore.storage.postgres.models.embedding import EMBEDDING_DIM

pytestmark = pytest.mark.integration

NOW = datetime(2026, 6, 6, 12, 0, tzinfo=UTC)
SLUG = "emb_cap"
STUB_MODEL = "emb-stub"


class _StubClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


class _StubPaidEmbedder:
    """A paid embedding backend returning fixed dim-correct vectors with no network."""

    model_name = "openai"
    model_version = STUB_MODEL
    is_paid = True
    provider = "openai"
    dim = EMBEDDING_DIM

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * EMBEDDING_DIM for _ in texts]


def _cleanup(session: Session) -> None:
    session.execute(delete(LlmSpend).where(LlmSpend.model == STUB_MODEL))
    ids = list(
        session.scalars(select(Source.id).where(Source.config["slug"].astext.like(f"{SLUG}%")))
    )
    if ids:
        doc_ids = list(session.scalars(select(Document.id).where(Document.source_id.in_(ids))))
        if doc_ids:  # embedding.document_id FKs document (RESTRICT) → delete embeddings first
            session.execute(delete(Embedding).where(Embedding.document_id.in_(doc_ids)))
        session.execute(delete(Document).where(Document.source_id.in_(ids)))
        session.execute(delete(RawDocument).where(RawDocument.source_id.in_(ids)))
        session.execute(delete(Source).where(Source.id.in_(ids)))


def _seed_doc(session: Session) -> tuple[UUID, str]:
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
    raw = RawDocument(
        source_id=source.id, content_hash=f"sha256:{new_id().hex}", path="a/b.gz", schema_version=1
    )
    session.add(raw)
    session.flush()
    content_hash = f"sha256:{SLUG}"
    result = upsert_document(
        session,
        DocumentRecord(
            source_id=source.id,
            raw_document_id=raw.id,
            canonical_url=f"https://{SLUG}.test/x",
            title="Doc",
            body_text="Some body text to embed into chunk vectors for the cap test.",
            published_at=NOW,
            content_hash=content_hash,
            parser_version="article@1.0.0",
        ),
        now=NOW,
    )
    return result.document_id, content_hash


def _spend_count(engine: Engine) -> int:
    with Session(engine) as session:
        return session.scalar(  # type: ignore[return-value]
            select(func.count()).select_from(LlmSpend).where(LlmSpend.model == STUB_MODEL)
        )


def test_paid_embed_under_cap_records_one_ledger_row(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)
        doc_id, content_hash = _seed_doc(session)

    with Session(migrated_engine) as session, session.begin():
        written = upsert_embeddings(
            session,
            document_id=doc_id,
            source_hash=content_hash,
            body_text="Some body text to embed into chunk vectors for the cap test.",
            backend=_StubPaidEmbedder(),
            clock=_StubClock(NOW),
        )
    assert written > 0  # the default 10 EUR cap is far above one tiny embed
    with Session(migrated_engine) as session:
        rows = list(session.scalars(select(LlmSpend).where(LlmSpend.model == STUB_MODEL)))
        assert len(rows) == 1
        assert rows[0].request_kind == "embedding"
        assert rows[0].output_tokens == 0

    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)


def test_paid_embed_over_cap_is_skipped(migrated_engine: Engine) -> None:
    from atlascore.core.settings import Settings, get_settings

    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)
        doc_id, content_hash = _seed_doc(session)
        # Pre-seed spend that blows past a tiny cap for this month.
        record_spend(
            session,
            provider="openai",
            model=STUB_MODEL,
            request_kind="summary",
            input_tokens=100_000_000,
            output_tokens=100_000_000,
            now=NOW,
        )

    get_settings.cache_clear()
    import atlascore.storage.embeddings as emb

    original = emb.get_settings
    emb.get_settings = lambda: Settings(llm_monthly_spend_cap_eur=0.0001)  # type: ignore[assignment]
    try:
        with Session(migrated_engine) as session, session.begin():
            written = upsert_embeddings(
                session,
                document_id=doc_id,
                source_hash=content_hash,
                body_text="Some body text to embed into chunk vectors for the cap test.",
                backend=_StubPaidEmbedder(),
                clock=_StubClock(NOW),
            )
    finally:
        emb.get_settings = original  # type: ignore[assignment]
        get_settings.cache_clear()

    assert written == 0  # over cap → skipped (graceful degradation)
    with Session(migrated_engine) as session:
        embeds = session.scalar(
            select(func.count()).select_from(Embedding).where(Embedding.document_id == doc_id)
        )
        assert embeds == 0  # no vectors written
    assert _spend_count(migrated_engine) == 1  # only the pre-seeded row; no new embedding spend

    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)
