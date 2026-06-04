"""``summary`` table — cached, cited LLM summaries with full provenance (docs/schemas §2.5).

Powers `get_document_summary`, single- and multi-doc. **R4 content-hash cache:** the cache key is
``unique(source_hash, model_name, model_version, prompt_version)`` where ``source_hash`` is a hash
over the **ordered** member ``content_hash``es — so any member change busts the cache and an
identical ``(content, model, prompt)`` tuple is never re-summarized. Multi-doc summaries set
``document_id = null`` and link members only via the ordered ``member_doc_ids[]``, so the purge
cascade (R3) must scan ``member_doc_ids`` (GIN) as well as ``document_id``.

Created via **raw SQL** in migration ``0008`` (the ``GIN(member_doc_ids)`` array index doesn't
round-trip through Alembic autogenerate), so the whole ``summary`` table is excluded from the
drift-check by owning table in ``migrations/env.py``. The ORM model maps columns for
querying/inserts only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, Text, Uuid, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from atlascore.core.ids import new_id
from atlascore.storage.postgres.base import Base


class Summary(Base):
    __tablename__ = "summary"

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=new_id)
    scope: Mapped[str] = mapped_column(Text(), nullable=False)  # single | multi
    document_id: Mapped[UUID | None] = mapped_column(
        Uuid(), ForeignKey("document.id", ondelete="RESTRICT"), nullable=True
    )
    member_doc_ids: Mapped[list[str]] = mapped_column(ARRAY(Text()), nullable=False)  # ordered
    source_hash: Mapped[str] = mapped_column(Text(), nullable=False)
    model_name: Mapped[str] = mapped_column(Text(), nullable=False)
    model_version: Mapped[str] = mapped_column(Text(), nullable=False)
    prompt_version: Mapped[str] = mapped_column(Text(), nullable=False)
    summary_text: Mapped[str] = mapped_column(Text(), nullable=False)
    citations: Mapped[dict[str, Any]] = mapped_column(JSONB(), nullable=False, default=dict)
    token_cost: Mapped[int | None] = mapped_column(Integer(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    schema_version: Mapped[int] = mapped_column(Integer(), nullable=False, default=1)
