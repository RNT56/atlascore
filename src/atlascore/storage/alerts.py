"""``alert`` persistence: idempotent creation, delivery bookkeeping, and the read side.

Creation is idempotent on ``dedupe_key`` (``ON CONFLICT DO NOTHING``) so re-evaluating the same fact
never double-fires. ``detail`` is run through ``sanitize_for_storage`` before write (§14).
:func:`list_alerts` is the ``get_alerts`` read path — it applies the status / type / `min_severity`
/ `since` filters and batch-resolves ``evidence_doc_ids`` to document/source provenance (a purged
doc simply drops out of the evidence list).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from atlascore.alerts.rules.base import AlertCandidate
from atlascore.schemas import SCHEMA_VERSION
from atlascore.schemas.alert import SEVERITY_ORDER, AlertStatus
from atlascore.security.secrets import sanitize_for_storage
from atlascore.storage.postgres.models import Alert, Document, Source

DEFAULT_ALERT_LIMIT = 50
MAX_ALERT_LIMIT = 100
_DEDUPE = "uq_alert_dedupe_key"


def upsert_alert(session: Session, candidate: AlertCandidate, *, now: datetime) -> bool:
    """Insert an alert idempotently on ``dedupe_key``; return True if a new row was created."""
    stmt = (
        insert(Alert)
        .values(
            alert_type=candidate.alert_type,
            rule_id=candidate.rule_id,
            severity=candidate.severity,
            status=AlertStatus.OPEN.value,
            title=candidate.title,
            dedupe_key=candidate.dedupe_key,
            evidence_doc_ids=candidate.evidence_doc_ids,
            detail=sanitize_for_storage(candidate.detail),
            created_at=now,
            schema_version=SCHEMA_VERSION,
        )
        .on_conflict_do_nothing(constraint=_DEDUPE)
        .returning(Alert.id)
    )
    return session.execute(stmt).first() is not None


def undelivered_alerts(session: Session, *, limit: int = 200) -> list[Alert]:
    """Open alerts not yet delivered (oldest first), for the delivery step."""
    return list(
        session.scalars(
            select(Alert)
            .where(Alert.delivered_at.is_(None))
            .order_by(Alert.created_at)
            .limit(limit)
        )
    )


def mark_delivered(session: Session, alert: Alert, *, status: str, now: datetime) -> None:
    """Record a delivery attempt's outcome on the alert."""
    alert.delivered_at = now
    alert.delivery_status = status


@dataclass(frozen=True)
class EvidenceRef:
    document_id: UUID
    source_name: str
    canonical_url: str | None


@dataclass(frozen=True)
class AlertRow:
    alert_id: UUID
    alert_type: str
    rule_id: str
    severity: str
    status: str
    title: str
    detail: dict[str, Any]
    evidence_doc_ids: list[str]
    created_at: datetime
    delivered_at: datetime | None
    delivery_status: str | None
    evidence: list[EvidenceRef] = field(default_factory=list)


def _resolve_evidence(session: Session, alerts: list[Alert]) -> dict[str, EvidenceRef]:
    """Batch-resolve every alert's evidence doc ids to document/source provenance."""
    ids: set[UUID] = set()
    for a in alerts:
        for raw in a.evidence_doc_ids or []:
            try:
                ids.add(UUID(raw))
            except ValueError:
                continue
    if not ids:
        return {}
    rows = session.execute(
        select(Document.id, Document.canonical_url, Source.name)
        .join(Source, Source.id == Document.source_id)
        .where(Document.id.in_(ids))
    )
    return {
        str(doc_id): EvidenceRef(doc_id, source_name, canonical_url)
        for doc_id, canonical_url, source_name in rows
    }


def list_alerts(
    session: Session,
    *,
    alert_types: list[str] | None = None,
    statuses: list[str] | None = None,
    min_severity: str | None = None,
    since: datetime | None = None,
    limit: int = DEFAULT_ALERT_LIMIT,
) -> list[AlertRow]:
    """Return recent alerts (newest first) with resolved evidence provenance, capped + filtered."""
    capped = max(1, min(limit, MAX_ALERT_LIMIT))
    stmt = select(Alert)
    if alert_types:
        stmt = stmt.where(Alert.alert_type.in_(alert_types))
    if statuses:
        stmt = stmt.where(Alert.status.in_(statuses))
    if min_severity is not None:
        floor = SEVERITY_ORDER.get(min_severity, 0)
        allowed = [sev for sev, rank in SEVERITY_ORDER.items() if rank >= floor]
        stmt = stmt.where(Alert.severity.in_(allowed))
    if since is not None:
        stmt = stmt.where(Alert.created_at >= since)
    stmt = stmt.order_by(Alert.created_at.desc(), Alert.id.desc()).limit(capped)

    alerts = list(session.scalars(stmt))
    evidence = _resolve_evidence(session, alerts)
    return [
        AlertRow(
            alert_id=a.id,
            alert_type=a.alert_type,
            rule_id=a.rule_id,
            severity=a.severity,
            status=a.status,
            title=a.title,
            detail=dict(a.detail or {}),
            evidence_doc_ids=list(a.evidence_doc_ids or []),
            created_at=a.created_at,
            delivered_at=a.delivered_at,
            delivery_status=a.delivery_status,
            evidence=[evidence[d] for d in (a.evidence_doc_ids or []) if d in evidence],
        )
        for a in alerts
    ]
