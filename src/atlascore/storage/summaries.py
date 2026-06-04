"""``summary`` persistence: content-hash cache key + cited-summary read/write (docs/schemas §2.5).

The R4 cache key is ``(source_hash, model_name, model_version, prompt_version)`` where
``source_hash`` hashes the **ordered** member ``content_hash``es. :func:`get_cached_summary` is the
compute-mode lookup (keyed to the active backend's identity); :func:`get_any_cached_summary` is the
cache-only lookup (any backend's summary for this content). :func:`insert_summary` is idempotent on
the cache key (a concurrent identical insert is a no-op + re-fetch). :func:`load_member_documents`
+ :func:`member_provenance` back the ``get_document_summary`` provenance bundle.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from atlascore.core.hashing import content_hash
from atlascore.schemas import SCHEMA_VERSION
from atlascore.storage.postgres.models import Document, Source, Summary

_CACHE_KEY = "summary_cache_key"


def compute_source_hash(member_content_hashes: list[str]) -> str:
    """Hash the ordered member ``content_hash``es into the cache's ``source_hash`` (R4)."""
    return content_hash("\n".join(member_content_hashes).encode("utf-8"))


@dataclass(frozen=True)
class SummaryView:
    summary_id: UUID
    scope: str
    summary_text: str
    citations: dict[str, list[str]]
    member_doc_ids: list[str]
    source_hash: str
    model_name: str
    model_version: str
    prompt_version: str
    token_cost: int | None
    created_at: datetime


@dataclass(frozen=True)
class MemberProvenance:
    document_id: UUID
    source_name: str
    canonical_url: str | None
    published_at: datetime | None


def to_view(summary: Summary) -> SummaryView:
    return SummaryView(
        summary_id=summary.id,
        scope=summary.scope,
        summary_text=summary.summary_text,
        citations=dict(summary.citations or {}),
        member_doc_ids=list(summary.member_doc_ids or []),
        source_hash=summary.source_hash,
        model_name=summary.model_name,
        model_version=summary.model_version,
        prompt_version=summary.prompt_version,
        token_cost=summary.token_cost,
        created_at=summary.created_at,
    )


def load_member_documents(session: Session, doc_ids: list[UUID]) -> list[Document] | None:
    """Load documents in the given order; ``None`` if any id is missing (preserves order)."""
    found = {d.id: d for d in session.scalars(select(Document).where(Document.id.in_(doc_ids)))}
    if len(found) != len(set(doc_ids)):
        return None
    return [found[d] for d in doc_ids]


def member_provenance(session: Session, doc_ids: list[UUID]) -> list[MemberProvenance]:
    """Return ordered document/source provenance for the member docs (for the tool output)."""
    rows = {
        doc.id: (doc, source)
        for doc, source in session.execute(
            select(Document, Source)
            .join(Source, Source.id == Document.source_id)
            .where(Document.id.in_(doc_ids))
        )
    }
    out: list[MemberProvenance] = []
    for did in doc_ids:
        pair = rows.get(did)
        if pair is None:
            continue
        doc, source = pair
        out.append(
            MemberProvenance(
                document_id=doc.id,
                source_name=source.name,
                canonical_url=doc.canonical_url,
                published_at=doc.published_at,
            )
        )
    return out


def get_cached_summary(
    session: Session,
    *,
    source_hash: str,
    model_name: str,
    model_version: str,
    prompt_version: str,
) -> Summary | None:
    """Compute-mode lookup: the cached summary for this exact (content, model, prompt) tuple."""
    return session.scalar(
        select(Summary).where(
            Summary.source_hash == source_hash,
            Summary.model_name == model_name,
            Summary.model_version == model_version,
            Summary.prompt_version == prompt_version,
        )
    )


def get_any_cached_summary(session: Session, *, source_hash: str) -> Summary | None:
    """Cache-only lookup: the most recent cached summary for this content, any backend."""
    return session.scalar(
        select(Summary)
        .where(Summary.source_hash == source_hash)
        .order_by(Summary.created_at.desc())
        .limit(1)
    )


def insert_summary(
    session: Session,
    *,
    doc_ids: list[UUID],
    source_hash: str,
    model_name: str,
    model_version: str,
    prompt_version: str,
    summary_text: str,
    citations: dict[str, list[str]],
    token_cost: int | None,
) -> Summary:
    """Insert a summary idempotently on the cache key; return the stored row.

    ``scope`` is ``single`` for one member (``document_id`` set) or ``multi`` (``document_id``
    null, members linked only via ``member_doc_ids[]``). A concurrent identical insert is a no-op
    (``ON CONFLICT DO NOTHING``) and the existing row is returned. Caller manages the transaction.
    """
    scope = "single" if len(doc_ids) == 1 else "multi"
    stmt = (
        insert(Summary)
        .values(
            scope=scope,
            document_id=doc_ids[0] if scope == "single" else None,
            member_doc_ids=[str(d) for d in doc_ids],
            source_hash=source_hash,
            model_name=model_name,
            model_version=model_version,
            prompt_version=prompt_version,
            summary_text=summary_text,
            citations=citations,
            token_cost=token_cost,
            schema_version=SCHEMA_VERSION,
        )
        .on_conflict_do_nothing(constraint=_CACHE_KEY)
    )
    session.execute(stmt)
    session.flush()
    stored = get_cached_summary(
        session,
        source_hash=source_hash,
        model_name=model_name,
        model_version=model_version,
        prompt_version=prompt_version,
    )
    assert stored is not None  # noqa: S101 — just inserted (or pre-existing via the conflict)
    return stored
