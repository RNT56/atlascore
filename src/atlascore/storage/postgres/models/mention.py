"""``mention`` table — an entity mentioned in a document (docs/schemas §2.9).

Powers ``get_entity_mentions``: each row links an ``entity`` to a ``document`` with a confidence,
optional ``sentiment`` (null when not computed — never fabricated), a **sanitized**
``context_snippet`` (evidence, not instruction), and the NER ``model_name``/``model_version``
provenance. ``mentioned_at`` mirrors the document's publish time so the
``btree(entity_id, mentioned_at)`` index serves "recent mentions of X" directly.

Created via **raw SQL** in migration ``0007`` for consistency with ``entity``/``embedding`` and
excluded from the autogenerate drift-check (``_include_object``). FKs are ``ON DELETE RESTRICT``
(R17) so the purge job drives deletion ordering.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Numeric, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from atlascore.core.ids import new_id
from atlascore.storage.postgres.base import Base


class Mention(Base):
    __tablename__ = "mention"

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=new_id)
    entity_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("entity.id", ondelete="RESTRICT"), nullable=False
    )
    document_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("document.id", ondelete="RESTRICT"), nullable=False
    )
    mentioned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(), nullable=True)
    sentiment: Mapped[Decimal | None] = mapped_column(Numeric(), nullable=True)
    context_snippet: Mapped[str | None] = mapped_column(Text(), nullable=True)
    model_name: Mapped[str] = mapped_column(Text(), nullable=False)
    model_version: Mapped[str] = mapped_column(Text(), nullable=False)
