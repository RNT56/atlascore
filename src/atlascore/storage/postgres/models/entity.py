"""``entity`` table — canonical entities with aliases + identifiers (docs/schemas §2.8).

Companies / assets / topics / people-as-entities, each with a ``canonical_name`` (trigram-indexed
for fuzzy name lookup), ``aliases``, external ``identifiers`` (CIK/LEI/ticker/wikidata), and a
``merged_into`` self-reference for merge history. ``get_entity_mentions`` resolves ``merged_into``
so a merged entity's mentions stay reachable from its surviving id.

Created via **raw SQL** in migration ``0007`` (the trigram ``gin (canonical_name gin_trgm_ops)``
and the array GINs cannot be round-tripped by Alembic autogenerate, mirroring the ``document``
GINs in 0003 and the partitioned ``time_series_point`` in 0006). The whole ``entity`` table and
everything named after it is therefore **excluded from the autogenerate drift-check** by
``_include_object`` in ``migrations/env.py``. The ORM model below maps the columns for
querying/upserts only; the real indexes/constraints live in the migration.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, Text, Uuid, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from atlascore.core.ids import new_id
from atlascore.storage.postgres.base import Base


class Entity(Base):
    __tablename__ = "entity"

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=new_id)
    entity_type: Mapped[str] = mapped_column(Text(), nullable=False)
    canonical_name: Mapped[str] = mapped_column(Text(), nullable=False)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(Text()), nullable=False, default=list)
    identifiers: Mapped[dict[str, Any]] = mapped_column(JSONB(), nullable=False, default=dict)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(), nullable=True)
    # Self-reference (merge history): a merged entity points at its surviving id (R17 RESTRICT).
    merged_into: Mapped[UUID | None] = mapped_column(
        Uuid(), ForeignKey("entity.id", ondelete="RESTRICT"), nullable=True
    )
    schema_version: Mapped[int] = mapped_column(Integer(), nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
