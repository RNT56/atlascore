"""``embedding`` persistence: idempotent, dedupe-before-embed chunk vectors (docs/schemas §2.6).

**R8 (fixed dim).** The backend's ``dim`` must equal the deployment's ``embedding`` column width
(:data:`EMBEDDING_DIM`); a mismatch is a config error, not a silent truncation — switching dim is
the documented backfill+reindex runbook.

**R13 (idempotent re-embedding).** Embeddings are keyed by the document's parsed-body fingerprint
(``source_hash`` = ``document.content_hash``). If the existing rows for this ``(document, model,
model_version)`` already carry that ``source_hash``, re-embedding is a **no-op skip** (mirroring the
summary cache rule, CLAUDE.md §20). A changed body (new ``content_hash`` on re-parse) → cache miss →
the old vectors are replaced. This is the "dedupe before embedding" cost rule in practice.
"""

from __future__ import annotations

import re
from decimal import Decimal
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from atlascore.core.clock import Clock, SystemClock
from atlascore.core.errors import ConfigError
from atlascore.core.logging import get_logger
from atlascore.core.settings import get_settings
from atlascore.enrichment.embeddings import EmbeddingBackend
from atlascore.storage.llm_spend import month_to_date_spend, period_ym, record_spend
from atlascore.storage.postgres.models import Embedding
from atlascore.storage.postgres.models.embedding import EMBEDDING_DIM

_log = get_logger("atlascore.storage.embeddings")

# Chunking bounds: keep chunks modest and cap their count so one huge document can't blow up
# embedding cost / row count. A chunk is split on whitespace near the limit.
CHUNK_MAX_CHARS = 1000
MAX_CHUNKS = 20

_WS_RE = re.compile(r"\s+")


def _estimate_tokens(texts: list[str]) -> int:
    """Rough input-token estimate (~4 chars/token) for embedding cost (§20).

    Conservative enough on purpose: the ``llm_spend`` price table uses chat-input rates, which
    overstate real embedding pricing by ~25x — so the hard cap trips early rather than late.
    """
    return max(1, sum(len(t) for t in texts) // 4)


def chunk_text(
    text: str, *, max_chars: int = CHUNK_MAX_CHARS, max_chunks: int = MAX_CHUNKS
) -> list[str]:
    """Split text into at most ``max_chunks`` whitespace-bounded chunks of ~``max_chars``."""
    normalized = _WS_RE.sub(" ", text).strip()
    if not normalized:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(normalized) and len(chunks) < max_chunks:
        end = min(start + max_chars, len(normalized))
        if end < len(normalized):
            # Back off to the last space so we don't split mid-word.
            space = normalized.rfind(" ", start, end)
            if space > start:
                end = space
        chunks.append(normalized[start:end].strip())
        start = end
    return [c for c in chunks if c]


def upsert_embeddings(
    session: Session,
    *,
    document_id: UUID,
    source_hash: str,
    body_text: str | None,
    backend: EmbeddingBackend,
    clock: Clock | None = None,
) -> int:
    """Embed a document's body into chunk vectors idempotently. Returns rows written (0 = skip).

    Caller manages the transaction. Returns 0 when the body is empty OR the stored embeddings for
    this ``(document, model)`` already match ``source_hash`` (the R13 no-op skip).

    A **paid** (cloud) backend (``is_paid``) is metered by ``LLM_MONTHLY_SPEND_CAP_EUR`` (§20),
    sharing the one monthly budget with summaries: if month-to-date spend is at/over the cap, the
    embedding is **skipped** (returns 0, leaving any existing vectors untouched — graceful
    degradation, FTS still works) rather than running uncapped. A successful paid embed records one
    ``llm_spend`` ledger row (``request_kind='embedding'``) in this same transaction.
    """
    if backend.dim != EMBEDDING_DIM:
        raise ConfigError(
            f"embedding backend dim {backend.dim} != deployment dim {EMBEDDING_DIM} "
            "(R8: switching dim is a backfill+reindex runbook, not an online change)"
        )
    chunks = chunk_text(body_text or "")
    if not chunks:
        return 0

    existing = list(
        session.scalars(
            select(Embedding).where(
                Embedding.document_id == document_id,
                Embedding.model_name == backend.model_name,
                Embedding.model_version == backend.model_version,
            )
        )
    )
    if existing and all(e.source_hash == source_hash for e in existing):
        return 0  # R13 no-op skip — unchanged content already embedded

    # §20 hard spend cap: a paid (cloud) backend at/over the month-to-date cap skips this embed
    # (checked BEFORE deleting any existing vectors, so a degraded run never strands a document).
    paid = bool(getattr(backend, "is_paid", False))
    now = (clock or SystemClock()).now()
    if paid:
        cap = Decimal(str(get_settings().llm_monthly_spend_cap_eur))
        if month_to_date_spend(session, period_ym(now)) >= cap:
            _log.warning(
                "embedding_skipped_over_cap",
                document_id=str(document_id),
                provider=getattr(backend, "provider", backend.model_name),
            )
            return 0

    if existing:
        session.execute(
            delete(Embedding).where(
                Embedding.document_id == document_id,
                Embedding.model_name == backend.model_name,
                Embedding.model_version == backend.model_version,
            )
        )

    vectors = backend.embed(chunks)
    for index, vector in enumerate(vectors):
        session.add(
            Embedding(
                document_id=document_id,
                chunk_index=index,
                model_name=backend.model_name,
                model_version=backend.model_version,
                dim=backend.dim,
                vector=vector,
                source_hash=source_hash,
            )
        )
    # §20: record the paid call's estimated cost in the same txn so the cap accrues cumulatively
    # across summaries AND embeddings (one shared monthly budget).
    if paid:
        record_spend(
            session,
            provider=getattr(backend, "provider", backend.model_name),
            model=backend.model_version,
            request_kind="embedding",
            input_tokens=_estimate_tokens(chunks),
            output_tokens=0,
            now=now,
        )
    session.flush()
    return len(vectors)
