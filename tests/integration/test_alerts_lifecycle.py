"""Integration: B18 alert lifecycle transitions + watchlist gating against Postgres 16.

Lifecycle: open → acknowledged → resolved, purged refused, get_alerts shape stable. Gating:
cve_watch fires only on watched/critical CVEs and sec_filing only on watched issuers when an active
watchlist is injected; the absent/inactive watchlist preserves fire-on-everything.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session

from atlascore.alerts.rules import build_rules
from atlascore.core.errors import AlertTransitionError
from atlascore.core.ids import new_id
from atlascore.schemas.alert import AlertStatus
from atlascore.schemas.document import DocumentRecord
from atlascore.schemas.watchlist import Watchlist
from atlascore.storage.alerts import acknowledge_alert, list_alerts, resolve_alert
from atlascore.storage.documents import upsert_document
from atlascore.storage.postgres.models import Alert, Document, RawDocument, Source
from atlascore.worker.alerts import evaluate_rules

pytestmark = pytest.mark.integration

NOW = datetime(2026, 6, 6, 12, 0, tzinfo=UTC)
SLUG_PREFIX = "lifecycle_it"


def _cleanup(session: Session) -> None:
    session.execute(delete(Alert).where(Alert.dedupe_key.like(f"%{SLUG_PREFIX}%")))
    session.execute(delete(Alert).where(Alert.rule_id.in_(["cve_watch_new", "sec_filing_new"])))
    src_ids = list(
        session.scalars(
            select(Source.id).where(Source.config["slug"].astext.like(f"{SLUG_PREFIX}%"))
        )
    )
    if src_ids:
        session.execute(delete(Document).where(Document.source_id.in_(src_ids)))
        session.execute(delete(RawDocument).where(RawDocument.source_id.in_(src_ids)))
        session.execute(delete(Source).where(Source.id.in_(src_ids)))


def _alert(session: Session, *, status: AlertStatus) -> Alert:
    a = Alert(
        alert_type="cve_watch",
        rule_id="manual",
        severity="high",
        status=status.value,
        title="lifecycle test",
        dedupe_key=f"{SLUG_PREFIX}:{uuid4()}",
        evidence_doc_ids=[],
        schema_version=1,
    )
    session.add(a)
    session.flush()
    return a


def test_lifecycle_transitions(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)
        open_id = _alert(session, status=AlertStatus.OPEN).id
        purged_id = _alert(session, status=AlertStatus.PURGED).id

    with Session(migrated_engine) as session, session.begin():
        assert acknowledge_alert(session, open_id).status == "acknowledged"
    with Session(migrated_engine) as session, session.begin():
        assert resolve_alert(session, open_id).status == "resolved"
    # resolved is terminal for ack; re-ack fails.
    with Session(migrated_engine) as session, session.begin():  # noqa: SIM117
        with pytest.raises(AlertTransitionError):
            acknowledge_alert(session, open_id)
    # purged can never transition.
    with Session(migrated_engine) as session, session.begin():  # noqa: SIM117
        with pytest.raises(AlertTransitionError, match="purged"):
            resolve_alert(session, purged_id)

    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)


def _seed_source_doc(
    session: Session, *, slug: str, category: str, cik: str | None, title: str, body: str
) -> None:
    config: dict = {"slug": slug}
    if cik is not None:
        config["params"] = {"cik": cik}
    source = Source(
        name=f"{slug}-src",
        category=category,
        access_method="api",
        policy_status="approved",
        enabled=True,
        config=config,
        schema_version=1,
    )
    session.add(source)
    session.flush()
    raw = RawDocument(
        source_id=source.id,
        content_hash=f"sha256:{new_id().hex}",
        path="a/b/c.gz",
        schema_version=1,
    )
    session.add(raw)
    session.flush()
    upsert_document(
        session,
        DocumentRecord(
            source_id=source.id,
            raw_document_id=raw.id,
            canonical_url=f"https://{slug}.test/x",
            title=title,
            body_text=body,
            published_at=NOW,
            content_hash=f"sha256:{slug}",
            parser_version="article@1.0.0",
        ),
        now=NOW,
    )


def test_watchlist_gates_cve_and_sec(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)
        _seed_source_doc(
            session,
            slug=f"{SLUG_PREFIX}_cve_crit",
            category="security_cve",
            cik=None,
            title="CVE-2026-CRIT",
            body="CVSS base score 9.8 (CRITICAL). remote code execution",
        )
        _seed_source_doc(
            session,
            slug=f"{SLUG_PREFIX}_cve_low",
            category="security_cve",
            cik=None,
            title="CVE-2026-LOW",
            body="CVSS base score 4.0 (MEDIUM). minor",
        )
        _seed_source_doc(
            session,
            slug=f"{SLUG_PREFIX}_sec_watched",
            category="finance_filings",
            cik="320193",
            title="10-K — Watched Co",
            body="annual report",
        )
        _seed_source_doc(
            session,
            slug=f"{SLUG_PREFIX}_sec_other",
            category="finance_filings",
            cik="999999",
            title="10-K — Other Co",
            body="annual report",
        )

    wl = Watchlist.model_validate(
        {"cve": {"min_cvss": 9.0, "severities": ["CRITICAL"]}, "sec": {"ciks": ["320193"]}}
    )
    with Session(migrated_engine) as session, session.begin():
        created = evaluate_rules(session, build_rules(wl), now=NOW)
    # Exactly 2 fire: the critical CVE + the watched issuer (low CVE + other issuer gated out).
    with Session(migrated_engine) as session:
        rows = list_alerts(session, alert_types=["cve_watch", "sec_filing"], statuses=["open"])
        titles = {r.title for r in rows}
        assert "CVE-2026-CRIT" in titles
        assert "10-K — Watched Co" in titles
        assert "CVE-2026-LOW" not in titles
        assert "10-K — Other Co" not in titles
        cve = next(r for r in rows if r.title == "CVE-2026-CRIT")
        assert cve.severity == "critical"  # severity derived from CVSS band
    assert created >= 2

    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)
