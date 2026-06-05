"""Read/write the runtime-owned incremental crawl cursor (``source.checkpoint``, B20).

The worker injects ``get_checkpoint`` into the connector's :class:`FetchContext` before a fetch and
persists the connector's ``next_checkpoint`` via ``set_checkpoint`` after a successful run, in the
same transaction that completes the job. The cursor is source-scoped (not per-job), runtime-owned
(never written by the YAML loader), and merge-updated so a connector can advance one key at a time.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from atlascore.storage.postgres.models import Source


def get_checkpoint(session: Session, source_id: UUID) -> dict[str, Any]:
    """Return the source's checkpoint dict (empty when unset)."""
    source = session.get(Source, source_id)
    if source is None or source.checkpoint is None:
        return {}
    return dict(source.checkpoint)


def set_checkpoint(session: Session, source_id: UUID, data: dict[str, Any]) -> None:
    """Merge ``data`` into the source's checkpoint (creates it when unset). Caller's txn."""
    source = session.get(Source, source_id)
    if source is None:
        return
    merged = dict(source.checkpoint or {})
    merged.update(data)
    source.checkpoint = merged
