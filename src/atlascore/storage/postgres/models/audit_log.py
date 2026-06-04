"""``audit_log`` table — the append-only audit trail (docs/schemas §2.11).

Records control-plane and MCP tool actions. ``detail`` is sanitized via
``sanitize_for_storage`` BEFORE write (CLAUDE.md §14) — it carries action + hashes for purge,
**never** secrets or personal data. ``retention_class='purge'`` entries are retained
indefinitely (erasure proof); ``routine`` rotates (R14).

**No foreign keys** (deliberate): the log is append-only and must survive purge/deletion of
the things it references — ``source_id`` and ``target_ref`` (a doc/entity id) are loose,
non-referential pointers (``target_ref`` is text precisely so a purged doc's audit entry,
recording only hashes, still stands). This mirrors the spec DDL sketch, which declares no FKs.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, Index, Text, Uuid, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from atlascore.core.ids import new_id
from atlascore.schemas.audit_log import Actor, RetentionClass
from atlascore.storage.postgres.base import Base

_ACTORS = ", ".join(f"'{a.value}'" for a in Actor)
_RETENTION_CLASSES = ", ".join(f"'{r.value}'" for r in RetentionClass)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=new_id)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    action: Mapped[str] = mapped_column(Text(), nullable=False)
    actor: Mapped[str] = mapped_column(Text(), nullable=False)
    tool_name: Mapped[str | None] = mapped_column(Text(), nullable=True)
    source_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    target_ref: Mapped[str | None] = mapped_column(Text(), nullable=True)
    retention_class: Mapped[str] = mapped_column(
        Text(), nullable=False, server_default=text("'routine'")
    )
    # detail is passed through sanitize_for_storage() before assignment (§14).
    detail: Mapped[dict[str, Any]] = mapped_column(
        JSONB(), nullable=False, server_default=text("'{}'::jsonb")
    )

    __table_args__ = (
        CheckConstraint(f"actor IN ({_ACTORS})", name="actor_allowed"),
        CheckConstraint(
            f"retention_class IN ({_RETENTION_CLASSES})", name="retention_class_allowed"
        ),
        Index("ix_audit_log_ts", "ts"),
        Index("ix_audit_log_action", "action"),
    )
