"""Document-backed alert rules: `sec_filing` (EDGAR) and `cve_watch` (NVD + vendor RSS).

Both scan canonical ``document`` rows by their source ``category`` and emit one alert per **watched**
document, citing it as evidence. The ``dedupe_key`` is keyed on the document id, so re-evaluation is
idempotent and a re-parsed document (same id) never re-fires. Work is bounded to the most recent
``_SCAN_LIMIT`` documents per category.

**Watchlist gating (B18).** When an *active* :class:`Watchlist` is supplied, `cve_watch` fires only
on watched/critical CVEs (CVSS ≥ floor, watched severity, matched cve_id, or keyword) and
`sec_filing` only on watched issuers (CIK, optional form). When the watchlist is absent/inactive,
both preserve the pre-B18 fire-on-everything behaviour (backward compatible). CVSS is read from a
parser-safe marker the NVD connector writes into the body (no schema change).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import ClassVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlascore.alerts.rules.base import AlertCandidate
from atlascore.schemas.alert import AlertType, Severity
from atlascore.schemas.watchlist import Watchlist
from atlascore.storage.postgres.models import Document, Source

# Bound the scan; dedupe makes re-runs cheap, but we don't re-scan unbounded history each tick.
_SCAN_LIMIT = 500
_CVSS_RE = re.compile(r"CVSS base score (\d+(?:\.\d+)?) \(([A-Z]+)\)")


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


def _extract_cvss(body_text: str | None) -> tuple[float | None, str | None]:
    """Read (score, severity) from the NVD connector's body marker, else (None, None)."""
    if not body_text:
        return None, None
    m = _CVSS_RE.search(body_text)
    if not m:
        return None, None
    return float(m.group(1)), m.group(2)


def _cvss_to_severity(score: float | None) -> str:
    """Map a CVSS base score to the AtlasCore severity scale."""
    if score is None:
        return Severity.MEDIUM.value
    if score >= 9.0:
        return Severity.CRITICAL.value
    if score >= 7.0:
        return Severity.HIGH.value
    if score >= 4.0:
        return Severity.MEDIUM.value
    if score > 0:
        return Severity.LOW.value
    return Severity.INFO.value


class SecFilingRule:
    """Fire a `sec_filing` alert per EDGAR filing (category `finance_filings`), watchlist-gated."""

    rule_id = "sec_filing_new"
    categories: ClassVar[list[str]] = ["finance_filings"]

    def __init__(self, watchlist: Watchlist | None = None) -> None:
        self._wl = watchlist

    def evaluate(self, session: Session, *, now: datetime) -> list[AlertCandidate]:
        wl = self._wl
        gate = wl is not None and wl.is_active and wl.sec.is_active
        candidates = []
        for doc, source in _recent_documents(session, self.categories):
            cik = (source.config or {}).get("params", {}).get("cik")
            form = (doc.title or "").split(" — ")[0].split(":")[0].strip().upper()
            if gate:
                assert wl is not None  # noqa: S101 — guarded by `gate`
                if not wl.sec.cik_matches(cik):
                    continue
                if wl.sec.forms and form not in wl.sec.forms:
                    continue
            detail = _detail(doc, source)
            detail["matched_cik"] = cik
            candidates.append(
                AlertCandidate(
                    alert_type=AlertType.SEC_FILING.value,
                    rule_id=self.rule_id,
                    severity=Severity.LOW.value,
                    title=doc.title or f"New SEC filing from {source.name}",
                    dedupe_key=f"{self.rule_id}:{doc.id}",
                    evidence_doc_ids=[str(doc.id)],
                    detail=detail,
                )
            )
        return candidates


class CveWatchRule:
    """Fire a `cve_watch` alert per NVD CVE / vendor-RSS advisory, watchlist + CVSS gated."""

    rule_id = "cve_watch_new"
    categories: ClassVar[list[str]] = ["security_cve"]

    def __init__(self, watchlist: Watchlist | None = None) -> None:
        self._wl = watchlist

    def _matches(
        self, wl: Watchlist, *, score: float | None, severity: str | None, doc: Document
    ) -> str | None:
        """Return the reason this CVE is watched, or None if it should be skipped."""
        cve = wl.cve
        if cve.min_cvss is not None and score is not None and score >= cve.min_cvss:
            return "min_cvss"
        if severity is not None and severity in cve.severities:
            return "severity"
        if doc.title and doc.title in cve.cve_ids:
            return "cve_id"
        haystack = f"{doc.title or ''} {doc.body_text or ''}".lower()
        if any(kw.lower() in haystack for kw in cve.keywords):
            return "keyword"
        return None

    def evaluate(self, session: Session, *, now: datetime) -> list[AlertCandidate]:
        wl = self._wl
        gate = wl is not None and wl.is_active and wl.cve.is_active
        candidates = []
        for doc, source in _recent_documents(session, self.categories):
            score, severity = _extract_cvss(doc.body_text)
            matched_by: str | None = "unfiltered"
            if gate:
                assert wl is not None  # noqa: S101 — guarded by `gate`
                matched_by = self._matches(wl, score=score, severity=severity, doc=doc)
                if matched_by is None:
                    continue
            detail = _detail(doc, source)
            detail.update(cvss_score=score, cvss_severity=severity, matched_by=matched_by)
            candidates.append(
                AlertCandidate(
                    alert_type=AlertType.CVE_WATCH.value,
                    rule_id=self.rule_id,
                    severity=_cvss_to_severity(score),
                    title=doc.title or f"New CVE/advisory from {source.name}",
                    dedupe_key=f"{self.rule_id}:{doc.id}",
                    evidence_doc_ids=[str(doc.id)],
                    detail=detail,
                )
            )
        return candidates
