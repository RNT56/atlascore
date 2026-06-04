"""Read helpers for ``source_health`` (powers ``atlas health`` / ``atlas digest`` and the
read-only ``get_source_health`` MCP tool).

Returns a flattened, **secret-free** view: the human-readable source name + slug and the health
metrics — never ``base_url``, ``credential_ref``, or any internal endpoint (docs/mcp §2.5).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlascore.storage.postgres.models import Source, SourceHealth


@dataclass(frozen=True)
class SourceHealthRow:
    """One source's health, joined with its (non-secret) identity."""

    source_id: UUID
    source_name: str
    source_slug: str | None
    status: str
    last_attempt_at: datetime | None
    last_success_at: datetime | None
    last_item_published_at: datetime | None
    freshness_lag: timedelta | None
    consecutive_failures: int
    success_rate_24h: float | None
    parse_quality: float | None
    duplicate_rate: float | None
    item_count_recent: int | None
    updated_at: datetime


def list_source_health(
    session: Session,
    *,
    statuses: list[str] | None = None,
    slugs: list[str] | None = None,
    limit: int | None = None,
) -> list[SourceHealthRow]:
    """Return health rows (joined to source name/slug), filtered by status/slug, ordered by name."""
    stmt = (
        select(SourceHealth, Source.name, Source.config["slug"].astext)
        .join(Source, Source.id == SourceHealth.source_id)
        .order_by(Source.name)
    )
    if statuses:
        stmt = stmt.where(SourceHealth.status.in_(statuses))
    if slugs:
        stmt = stmt.where(Source.config["slug"].astext.in_(slugs))
    if limit is not None:
        stmt = stmt.limit(limit)

    rows: list[SourceHealthRow] = []
    for health, name, slug in session.execute(stmt).all():
        rows.append(
            SourceHealthRow(
                source_id=health.source_id,
                source_name=name,
                source_slug=slug,
                status=health.status,
                last_attempt_at=health.last_attempt_at,
                last_success_at=health.last_success_at,
                last_item_published_at=health.last_item_published_at,
                freshness_lag=health.freshness_lag,
                consecutive_failures=health.consecutive_failures,
                success_rate_24h=health.success_rate_24h,
                parse_quality=health.parse_quality,
                duplicate_rate=health.duplicate_rate,
                item_count_recent=health.item_count_recent,
                updated_at=health.updated_at,
            )
        )
    return rows
