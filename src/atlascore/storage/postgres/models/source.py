"""``source`` table — the source registry (docs/schemas/README.md §2.1).

YAML is the source of truth and is mirrored here. Default-deny: ``policy_status``
defaults to ``unknown`` and ``enabled`` to ``false``. ``credential_ref`` names an env
key, never a secret value (CLAUDE.md §3). ``config`` jsonb is the round-trip home for
``allowed_uses``/``robots``/``connector``/``schedule``/``timeout_seconds`` (R9).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from atlascore.core.ids import new_id
from atlascore.schemas.source import AccessMethod, PolicyStatus
from atlascore.storage.postgres.base import Base

_ACCESS_METHODS = ", ".join(f"'{m.value}'" for m in AccessMethod)
_POLICY_STATUSES = ", ".join(f"'{s.value}'" for s in PolicyStatus)


class Source(Base):
    __tablename__ = "source"

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(Text(), nullable=False, unique=True)
    category: Mapped[str] = mapped_column(Text(), nullable=False)
    access_method: Mapped[str] = mapped_column(Text(), nullable=False)
    base_url: Mapped[str | None] = mapped_column(Text(), nullable=True)
    policy_status: Mapped[str] = mapped_column(
        Text(), nullable=False, server_default=text("'unknown'")
    )
    rate_limit: Mapped[dict[str, Any]] = mapped_column(
        JSONB(), nullable=False, server_default=text("'{}'::jsonb")
    )
    retention: Mapped[dict[str, Any]] = mapped_column(
        JSONB(), nullable=False, server_default=text("'{}'::jsonb")
    )
    freshness: Mapped[dict[str, Any]] = mapped_column(
        JSONB(), nullable=False, server_default=text("'{}'::jsonb")
    )
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB(), nullable=False, server_default=text("'{}'::jsonb")
    )
    # Runtime-owned incremental crawl cursor (B20). NULL = no cursor yet (full fetch). Unlike
    # ``config`` (the lossless YAML round-trip home, overwritten on every `atlas source sync`),
    # ``checkpoint`` is never written by the YAML loader — only by the worker after a fetch.
    checkpoint: Mapped[dict[str, Any] | None] = mapped_column(JSONB(), nullable=True)
    parser_ref: Mapped[str | None] = mapped_column(Text(), nullable=True)
    credential_ref: Mapped[str | None] = mapped_column(Text(), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean(), nullable=False, server_default=text("false"))
    # No server_default by design: docs/schemas §2.1 DDL declares `schema_version
    # integer NOT NULL` with no default. Every writer sets it (ORM default=1 here; the
    # B2 YAML loader sets it explicitly). Adding a server_default would diverge from
    # the authoritative DDL (and the autogenerate drift-check).
    schema_version: Mapped[int] = mapped_column(Integer(), nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # The `name=` here is the convention's %(constraint_name)s TOKEN, not the final
    # name: Base.metadata's naming_convention expands it to "ck_source_<token>" (and
    # Alembic's op applies the same convention in migration 0001, so model and DB agree
    # on "ck_source_access_method_allowed" / "ck_source_policy_status_allowed"). Locked
    # by tests/unit/test_source_table.py — do NOT pre-prefix the token (it would double).
    __table_args__ = (
        CheckConstraint(f"access_method IN ({_ACCESS_METHODS})", name="access_method_allowed"),
        CheckConstraint(f"policy_status IN ({_POLICY_STATUSES})", name="policy_status_allowed"),
        Index("ix_source_category_enabled", "category", "enabled"),
        Index("ix_source_policy_status", "policy_status"),
    )
