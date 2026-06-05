"""Raw-document read path for the `get_raw_document` MCP tool (docs/mcp §2.7, R7).

Reads the immutable, content-addressed raw payload off disk (via :class:`RawStore`) plus its
``raw_document`` provenance, resolved either from a ``document_id`` (the common case) or directly
from a ``raw_document_id``. The MCP tool decides whether to return the **sanitized** text (default)
or the **raw** bytes (``include_raw_html=true``, audited + wrapped); this layer just surfaces the raw
bytes + provenance and never sanitizes — keeping the sanitize/raw decision in one place (the tool).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlascore.storage.postgres.models import Document, RawDocument, Source
from atlascore.storage.rawstore import RawStore


@dataclass(frozen=True)
class RawDocumentContent:
    """Raw payload bytes + provenance for one document/raw row (no sanitization applied here)."""

    raw_document_id: UUID
    document_id: UUID | None
    raw_bytes: bytes
    mime_type: str | None
    content_hash: str
    size_bytes: int
    http_status: int | None
    etag: str | None
    last_modified: str | None
    fetched_at: datetime | None
    source_id: UUID
    source_name: str
    canonical_url: str | None


def get_raw_document_content(
    session: Session,
    raw_store: RawStore,
    *,
    document_id: UUID | None = None,
    raw_document_id: UUID | None = None,
) -> RawDocumentContent | None:
    """Resolve + read a raw payload by ``document_id`` (preferred) or ``raw_document_id``.

    Returns ``None`` if the document/raw row doesn't exist (e.g. purged). Caller owns the session.
    """
    document: Document | None = None
    if document_id is not None:
        document = session.get(Document, document_id)
        if document is None:
            return None
        raw_document_id = document.raw_document_id
    if raw_document_id is None:
        return None

    raw = session.get(RawDocument, raw_document_id)
    if raw is None:
        return None
    source = session.get(Source, raw.source_id)
    if source is None:
        return None

    # If only a raw_document_id was given, surface a representative document's canonical_url.
    canonical_url = document.canonical_url if document is not None else None
    if document is None:
        canonical_url = session.scalar(
            select(Document.canonical_url)
            .where(Document.raw_document_id == raw_document_id)
            .where(Document.canonical_url.is_not(None))
            .limit(1)
        )

    raw_bytes = raw_store.get(raw.path)
    return RawDocumentContent(
        raw_document_id=raw.id,
        document_id=document.id if document is not None else None,
        raw_bytes=raw_bytes,
        mime_type=raw.mime_type,
        content_hash=raw.content_hash,
        size_bytes=raw.size_bytes if raw.size_bytes is not None else len(raw_bytes),
        http_status=raw.http_status,
        etag=raw.etag,
        last_modified=raw.last_modified,
        fetched_at=raw.fetched_at,
        source_id=source.id,
        source_name=source.name,
        canonical_url=canonical_url,
    )
