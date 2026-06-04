"""Document-backed alert rules: `sec_filing` (EDGAR) and `cve_watch` (NVD + vendor RSS).

Both scan canonical ``document`` rows by their source ``category`` and emit one alert per document,
citing that document as evidence. The ``dedupe_key`` is keyed on the document id, so re-evaluation
is idempotent and a re-parsed document (same id) never re-fires. Work is bounded to the most recent
``_SCAN_LIMIT`` documents per category (single-operator scale; older items already alerted).
"""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlascore.alerts.rules.base import AlertCandidate
from atlascore.schemas.alert import AlertType, Severity
from atlascore.storage.postgres.models import Document, Source

# Bound the scan; dedupe makes re-runs cheap, but we don't re-scan unbounded history each tick.
_SCAN_LIMIT = 500


def _recent_documents(session: Session, categories: list[str]) -> list[tuple[Document, Source]]:
    stmt = (
        select(Document, Source)
        .join(Source, Source.id == Document.source_id)
        .where(Source.category.in_(categories))
        .order_by(Document.ingested_at.desc())
        .limit(_SCAN_LIMIT)
    )
    return [(doc, source) for doc, source in session.execute(stmt).all()]


def _detail(doc: Document, source: Source) -> dict[str, object]:
    return {
        "source": source.name,
        "canonical_url": doc.canonical_url,
        "published_at": doc.published_at.isoformat() if doc.published_at else None,
    }


class SecFilingRule:
    """Fire one `sec_filing` alert per EDGAR filing document (category `finance_filings`)."""

    rule_id = "sec_filing_new"
    categories: ClassVar[list[str]] = ["finance_filings"]

    def evaluate(self, session: Session, *, now: datetime) -> list[AlertCandidate]:
        candidates = []
        for doc, source in _recent_documents(session, self.categories):
            candidates.append(
                AlertCandidate(
                    alert_type=AlertType.SEC_FILING.value,
                    rule_id=self.rule_id,
                    severity=Severity.LOW.value,
                    title=doc.title or f"New SEC filing from {source.name}",
                    dedupe_key=f"{self.rule_id}:{doc.id}",
                    evidence_doc_ids=[str(doc.id)],
                    detail=_detail(doc, source),
                )
            )
        return candidates


class CveWatchRule:
    """Fire one `cve_watch` alert per NVD CVE / vendor-RSS advisory (category `security_cve`)."""

    rule_id = "cve_watch_new"
    categories: ClassVar[list[str]] = ["security_cve"]

    def evaluate(self, session: Session, *, now: datetime) -> list[AlertCandidate]:
        candidates = []
        for doc, source in _recent_documents(session, self.categories):
            candidates.append(
                AlertCandidate(
                    alert_type=AlertType.CVE_WATCH.value,
                    rule_id=self.rule_id,
                    severity=Severity.MEDIUM.value,
                    title=doc.title or f"New CVE/advisory from {source.name}",
                    dedupe_key=f"{self.rule_id}:{doc.id}",
                    evidence_doc_ids=[str(doc.id)],
                    detail=_detail(doc, source),
                )
            )
        return candidates
