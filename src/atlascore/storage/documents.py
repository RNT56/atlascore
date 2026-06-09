"""Canonical ``document`` persistence: last-write-wins upsert (docs/schemas §2.4, R2).

A parsed document is identified by its ``canonical_url`` (the partial-unique key): if a row
with that URL exists, re-parsing **updates it in place** (last-write-wins) — but only the
parser-owned fields. Enrichment-owned columns (``topics``, ``entity_ids``, ``cluster_id``)
are left untouched so a re-parse never wipes enrichment (those are reconciled in B11/purge).

``content_hash`` (of the parsed body) is the change detector: an unchanged hash makes the
upsert a no-op (``unchanged``), so replays and re-parses are idempotent and cheap.

Null-``canonical_url`` documents can't use the unique index; they dedupe best-effort on
``(source_id, raw_document_id, content_hash)``, where ``content_hash`` folds in the feed GUID
(see ``worker/parse.py``) so two DISTINCT link-less items with an identical title+body are NOT
collapsed into one row. Remaining honest limitation (CLAUDE.md §16): because the prior-row
match is by ``content_hash``, a CHANGED body on a re-parse of a null-URL item won't match its
old row and inserts a new one (over-insert, not data loss). Real feed items almost always carry
a link (so they dedupe on ``canonical_url``), making this a rare, documented edge.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlascore.schemas.document import DocumentRecord
from atlascore.storage.postgres.models import Document, Source

UpsertOutcome = Literal["created", "updated", "unchanged"]


@dataclass(frozen=True)
class DocumentUpsert:
    """The result of one :func:`upsert_document` call."""

    document_id: UUID
    outcome: UpsertOutcome
    changed: bool


def _find_existing(session: Session, record: DocumentRecord) -> Document | None:
    if record.canonical_url is not None:
        return session.scalar(
            select(Document).where(Document.canonical_url == record.canonical_url)
        )
    return session.scalar(
        select(Document).where(
            Document.source_id == record.source_id,
            Document.raw_document_id == record.raw_document_id,
            Document.canonical_url.is_(None),
            Document.content_hash == record.content_hash,
        )
    )


def upsert_document(session: Session, record: DocumentRecord, *, now: datetime) -> DocumentUpsert:
    """Insert or last-write-wins update a ``document``. Caller manages the transaction."""
    existing = _find_existing(session, record)
    if existing is None:
        doc = Document(
            source_id=record.source_id,
            raw_document_id=record.raw_document_id,
            canonical_url=record.canonical_url,
            title=record.title,
            body_text=record.body_text,
            language=record.language,
            published_at=record.published_at,
            source_local_published_at=record.source_local_published_at,
            parsed_at=record.parsed_at or now,
            content_hash=record.content_hash,
            parser_version=record.parser_version,
            schema_version=record.schema_version,
        )
        if record.ingested_at is not None:
            doc.ingested_at = record.ingested_at
        session.add(doc)
        session.flush()
        return DocumentUpsert(doc.id, "created", changed=True)

    if existing.content_hash == record.content_hash:
        return DocumentUpsert(existing.id, "unchanged", changed=False)

    # Last-write-wins: refresh parser-owned fields only (R2). Enrichment columns
    # (topics/entity_ids/cluster_id) and ingested_at are intentionally preserved.
    existing.raw_document_id = record.raw_document_id
    existing.title = record.title
    existing.body_text = record.body_text
    existing.language = record.language
    existing.published_at = record.published_at
    existing.source_local_published_at = record.source_local_published_at
    existing.parsed_at = record.parsed_at or now
    existing.content_hash = record.content_hash
    existing.parser_version = record.parser_version
    existing.schema_version = record.schema_version
    session.flush()
    return DocumentUpsert(existing.id, "updated", changed=True)


@dataclass(frozen=True)
class DocumentView:
    """A single document for the WebUI detail page (sanitized body + provenance; no secrets)."""

    document_id: UUID
    source_name: str
    source_slug: str | None
    canonical_url: str | None
    title: str | None
    body_text: str | None
    language: str | None
    topics: list[str]
    entity_ids: list[str]
    published_at: datetime | None
    ingested_at: datetime | None
    parser_version: str
    content_hash: str


def get_document_view(session: Session, document_id: UUID) -> DocumentView | None:
    """Return one document joined to its (non-secret) source identity, or None if absent."""
    row = session.execute(
        select(Document, Source.name, Source.config["slug"].astext)
        .join(Source, Source.id == Document.source_id)
        .where(Document.id == document_id)
    ).first()
    if row is None:
        return None
    doc, source_name, source_slug = row
    return DocumentView(
        document_id=doc.id,
        source_name=source_name,
        source_slug=source_slug,
        canonical_url=doc.canonical_url,
        title=doc.title,
        body_text=doc.body_text,
        language=doc.language,
        topics=list(doc.topics or []),
        entity_ids=list(doc.entity_ids or []),
        published_at=doc.published_at,
        ingested_at=doc.ingested_at,
        parser_version=doc.parser_version,
        content_hash=doc.content_hash,
    )
