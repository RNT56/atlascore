"""Integration: B21 LLM spend ledger + spend-cap enforcement on Postgres 16 (migration 0012)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session

from atlascore.core.ids import new_id
from atlascore.enrichment.summaries.base import SummaryInput, SummaryResult
from atlascore.enrichment.summaries.pipeline import summarize_documents
from atlascore.schemas.document import DocumentRecord
from atlascore.storage.documents import upsert_document
from atlascore.storage.llm_spend import month_to_date_spend, record_spend
from atlascore.storage.postgres.models import (
    Document,
    LlmSpend,
    RawDocument,
    Source,
    Summary,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 6, 6, 12, 0, tzinfo=UTC)
SLUG = "spend_it"


class _StubClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


class _StubPaidBackend:
    """A paid backend that returns a fixed result without any network (B21 spend-cap test)."""

    model_name = "openai"
    model_version = "gpt-stub"
    prompt_version = "hosted@1"
    is_paid = True
    provider = "openai"

    def summarize(self, docs: list[SummaryInput]) -> SummaryResult:
        return SummaryResult(
            summary_text="stub summary",
            citations={d.document_id: [] for d in docs},
            token_cost=3000,
            input_tokens=2000,
            output_tokens=1000,
        )


def _cleanup(session: Session) -> None:
    session.execute(delete(LlmSpend).where(LlmSpend.model == "gpt-stub"))
    ids = list(
        session.scalars(select(Source.id).where(Source.config["slug"].astext.like(f"{SLUG}%")))
    )
    if ids:
        doc_ids = list(session.scalars(select(Document.id).where(Document.source_id.in_(ids))))
        if doc_ids:  # summary.document_id FKs document → delete summaries first
            session.execute(delete(Summary).where(Summary.document_id.in_(doc_ids)))
        session.execute(delete(Document).where(Document.source_id.in_(ids)))
        session.execute(delete(RawDocument).where(RawDocument.source_id.in_(ids)))
        session.execute(delete(Source).where(Source.id.in_(ids)))


def _seed_doc(session: Session) -> UUID:
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
    result = upsert_document(
        session,
        DocumentRecord(
            source_id=source.id,
            raw_document_id=raw.id,
            canonical_url=f"https://{SLUG}.test/x",
            title="Doc",
            body_text="Some body text to summarize.",
            published_at=NOW,
            content_hash=f"sha256:{SLUG}",
            parser_version="article@1.0.0",
        ),
        now=NOW,
    )
    return result.document_id


def test_record_and_month_to_date_spend(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)
        record_spend(
            session,
            provider="openai",
            model="gpt-stub",
            request_kind="summary",
            input_tokens=1000,
            output_tokens=1000,
            now=datetime(2026, 6, 1, tzinfo=UTC),
        )
        record_spend(
            session,
            provider="openai",
            model="gpt-stub",
            request_kind="summary",
            input_tokens=1000,
            output_tokens=1000,
            now=datetime(2026, 6, 20, tzinfo=UTC),
        )
        record_spend(
            session,
            provider="openai",
            model="gpt-stub",
            request_kind="summary",
            input_tokens=1000,
            output_tokens=1000,
            now=datetime(2026, 7, 1, tzinfo=UTC),
        )
    with Session(migrated_engine) as session:
        # June sums two rows (2 * 0.002), July one (0.002) — months are isolated.
        assert month_to_date_spend(session, "2026-06") == Decimal("0.004000")
        assert month_to_date_spend(session, "2026-07") == Decimal("0.002000")
    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)


def test_paid_compute_records_under_cap(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)
        doc_id = _seed_doc(session)

    # Patch the cap via settings: estimate cost for 2000/1000 openai tokens = ~0.0025 EUR << 10.
    with Session(migrated_engine) as session, session.begin():
        outcome = summarize_documents(
            session, [doc_id], backend=_StubPaidBackend(), clock=_StubClock(NOW)
        )
    assert outcome.found is True and outcome.summary is not None
    with Session(migrated_engine) as session:
        spend_rows = session.scalar(
            select(func.count()).select_from(LlmSpend).where(LlmSpend.model == "gpt-stub")
        )
        assert spend_rows == 1  # exactly one ledger row for the paid compute

    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)


def test_over_cap_degrades_to_cache_only(migrated_engine: Engine) -> None:
    from atlascore.core.settings import Settings, get_settings

    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)
        doc_id = _seed_doc(session)
        # Pre-seed spend at/over a tiny cap for this month.
        record_spend(
            session,
            provider="openai",
            model="gpt-stub",
            request_kind="summary",
            input_tokens=100_000_000,
            output_tokens=100_000_000,
            now=NOW,
        )

    # Force a tiny cap so the pre-seeded spend exceeds it.
    get_settings.cache_clear()
    import atlascore.enrichment.summaries.pipeline as pipe

    original = pipe.get_settings
    pipe.get_settings = lambda: Settings(llm_monthly_spend_cap_eur=0.0001)  # type: ignore[assignment]
    try:
        with Session(migrated_engine) as session, session.begin():
            outcome = summarize_documents(
                session, [doc_id], backend=_StubPaidBackend(), clock=_StubClock(NOW)
            )
    finally:
        pipe.get_settings = original  # type: ignore[assignment]
        get_settings.cache_clear()

    assert outcome.found is False
    assert outcome.reason == "LLM monthly spend cap reached; cache-only"
    with Session(migrated_engine) as session:
        # No NEW spend row beyond the pre-seeded one (compute was skipped).
        rows = session.scalar(
            select(func.count()).select_from(LlmSpend).where(LlmSpend.model == "gpt-stub")
        )
        assert rows == 1

    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)
