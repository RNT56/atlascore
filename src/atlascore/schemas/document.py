"""The ``document`` canonical record (docs/schemas/README.md §2.4).

The current-state normalized record (one row per logical document). Re-parsing is
**last-write-wins** in place (R2): old parsed output is not retained but is reproducible
by re-running the pinned ``parser_version`` against the immutable ``raw_document``.
``content_hash`` is the hash of the parsed body — it detects when a re-parse changed
output. The summary is NOT here; it lives in the ``summary`` table.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DocumentRecord(BaseModel):
    """A ``document`` table row produced by a parser. Attribution + provenance fields
    (``source_id``, ``raw_document_id``, ``parser_version``, ``schema_version``) are
    required so every document traces back to its evidence (CLAUDE.md §10)."""

    model_config = ConfigDict(extra="forbid")

    id: UUID | None = None
    source_id: UUID
    raw_document_id: UUID
    canonical_url: str | None = None
    title: str | None = None
    body_text: str | None = None
    language: str | None = None
    topics: list[str] = Field(default_factory=list)
    entity_ids: list[str] = Field(default_factory=list)
    published_at: datetime | None = None
    source_local_published_at: datetime | None = None
    ingested_at: datetime | None = None
    parsed_at: datetime | None = None
    content_hash: str
    cluster_id: UUID | None = None
    parser_version: str
    schema_version: int = 1

    @field_validator("canonical_url")
    @classmethod
    def _http_only_url(cls, value: str | None) -> str | None:
        """Coerce a non-http(s) ``canonical_url`` to None (defense in depth: it becomes an href in
        the WebUI, where ``javascript:``/``data:`` schemes are an XSS vector; the article parser
        already strips these, this guards any other record producer)."""
        if value is None:
            return None
        return value if value.strip().lower().startswith(("http://", "https://")) else None
