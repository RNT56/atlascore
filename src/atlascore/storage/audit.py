"""``audit_log`` writes — the single sanitized entry point (docs/schemas §2.11, CLAUDE.md §14).

Every audit write goes through :func:`write_audit_log`, which runs ``detail`` through
``sanitize_for_storage`` BEFORE it reaches the jsonb column — so no secret or credential-bearing
URL can ever land in the audit trail or its backups. ``ts`` and ``id`` come from DB/ORM defaults
(``ts`` = authoritative DB time). The caller owns the transaction.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from atlascore.schemas.audit_log import Actor, RetentionClass
from atlascore.security.secrets import sanitize_for_storage
from atlascore.storage.postgres.models import AuditLog


def write_audit_log(
    session: Session,
    *,
    action: str,
    actor: Actor,
    tool_name: str | None = None,
    source_id: UUID | None = None,
    target_ref: str | None = None,
    detail: dict[str, Any] | None = None,
    retention_class: RetentionClass = RetentionClass.ROUTINE,
) -> AuditLog:
    """Append one sanitized ``audit_log`` row and return it (flushed, id assigned)."""
    row = AuditLog(
        action=action,
        actor=actor.value,
        tool_name=tool_name,
        source_id=source_id,
        target_ref=target_ref,
        retention_class=retention_class.value,
        detail=sanitize_for_storage(detail or {}),
    )
    session.add(row)
    session.flush()
    return row
