"""Integration: B13 alert rules + delivery + `get_alerts` against Postgres 16.

The B13 done-when: each alert fires with dedupe + `evidence_doc_ids` and is queryable via MCP. Seeds
an SEC filing doc, an NVD CVE doc, a FRED observation, and a stale source_health row, then exercises:
rule evaluation (idempotent — re-run creates 0), delivery (LogChannel marks delivered), the
`get_alerts` MCP tool (+ min_severity / type filters), and evidence provenance.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session

from atlascore.alerts.delivery import LogChannel
from atlascore.alerts.rules import ALL_RULES
from atlascore.core.ids import new_id
from atlascore.mcp_server import TOOL_GET_ALERTS, build_mcp_server
from atlascore.schemas.alert import AlertType
from atlascore.schemas.document import DocumentRecord
from atlascore.storage.documents import upsert_document
from atlascore.storage.postgres.models import (
    Alert,
    Document,
    RawDocument,
    Source,
    SourceHealth,
    TimeSeriesPoint,
)
from atlascore.worker.alerts import deliver_pending, evaluate_rules, run_alerts

pytestmark = pytest.mark.integration

NOW = datetime(2026, 6, 6, 12, 0, 0, tzinfo=UTC)
SEC_SLUG = "alert_sec_it"
CVE_SLUG = "alert_cve_it"
SERIES = "ALERTCPI"


def _cleanup(session: Session) -> None:
    # No FK references alert (evidence_doc_ids is a non-referential array), and no other test
    # creates alerts, so wiping the alert table is the clean, correct reset in this isolated DB.
    session.execute(delete(Alert))
    session.execute(delete(TimeSeriesPoint).where(TimeSeriesPoint.series_id == SERIES))
    src_ids = list(
        session.scalars(
            select(Source.id).where(Source.config["slug"].astext.in_([SEC_SLUG, CVE_SLUG]))
        )
    )
    if src_ids:
        session.execute(delete(SourceHealth).where(SourceHealth.source_id.in_(src_ids)))
        session.execute(delete(Document).where(Document.source_id.in_(src_ids)))
        session.execute(delete(RawDocument).where(RawDocument.source_id.in_(src_ids)))
        session.execute(delete(Source).where(Source.id.in_(src_ids)))


def _seed(session: Session) -> dict[str, object]:
    out: dict[str, object] = {}
    # An SEC filing document + an NVD CVE document (categories drive the rules).
    for slug, category, title, url in [
        (SEC_SLUG, "finance_filings", "10-K — Apple Inc", "https://sec.test/aapl-10k"),
        (
            CVE_SLUG,
            "security_cve",
            "CVE-2026-0001 buffer overflow",
            "https://nvd.test/CVE-2026-0001",
        ),
    ]:
        source = Source(
            name=f"alert-src-{slug}",
            category=category,
            access_method="api",
            policy_status="approved",
            enabled=True,
            config={"slug": slug},
            schema_version=1,
        )
        session.add(source)
        session.flush()
        raw = RawDocument(
            source_id=source.id,
            content_hash=f"sha256:{new_id().hex}",
            path="ab/cd/ef.gz",
            schema_version=1,
        )
        session.add(raw)
        session.flush()
        result = upsert_document(
            session,
            DocumentRecord(
                source_id=source.id,
                raw_document_id=raw.id,
                canonical_url=url,
                title=title,
                body_text=f"{title} body text.",
                published_at=datetime(2026, 6, 5, tzinfo=UTC),
                content_hash=f"sha256:{slug}",
                parser_version="article@1.0.0",
            ),
            now=NOW,
        )
        out[slug] = result.document_id
        if category == "finance_filings":
            # Mark this source stale so the freshness rule fires too.
            session.add(
                SourceHealth(
                    source_id=source.id,
                    status="stale",
                    consecutive_failures=5,
                    freshness_lag=timedelta(days=4),
                    updated_at=NOW,
                )
            )
    # A FRED observation for the macro_release rule.
    session.add(
        TimeSeriesPoint(
            ts=datetime(2026, 6, 1, tzinfo=UTC),
            provider="fred",
            series_id=SERIES,
            kind="observation",
            value=Decimal("314.2"),
            unit="Index",
            schema_version=1,
        )
    )
    return out


def test_rules_fire_with_evidence_and_are_idempotent(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)
        ids = _seed(session)

    # First evaluation creates the alerts.
    with Session(migrated_engine) as session, session.begin():
        created = evaluate_rules(session, ALL_RULES, now=NOW)
    assert created >= 4  # sec_filing + cve_watch + macro_release + freshness

    with Session(migrated_engine) as session:
        by_type = {
            a.alert_type: a for a in session.scalars(select(Alert).where(Alert.created_at == NOW))
        }
        # sec_filing + cve_watch cite their document as evidence.
        assert by_type[AlertType.SEC_FILING.value].evidence_doc_ids == [str(ids[SEC_SLUG])]
        assert by_type[AlertType.CVE_WATCH.value].evidence_doc_ids == [str(ids[CVE_SLUG])]
        # macro_release + freshness carry their evidence in detail (no documents).
        assert by_type[AlertType.MACRO_RELEASE.value].detail["series_id"] == SERIES
        assert by_type[AlertType.FRESHNESS.value].detail["status"] == "stale"
        assert all(a.status == "open" for a in by_type.values())

    # Re-evaluation is idempotent (dedupe_key) — no new alerts.
    with Session(migrated_engine) as session, session.begin():
        again = evaluate_rules(session, ALL_RULES, now=NOW + timedelta(minutes=1))
    assert again == 0

    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)


def test_delivery_marks_delivered(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)
        _seed(session)
        evaluate_rules(session, ALL_RULES, now=NOW)

    with Session(migrated_engine) as session, session.begin():
        delivered, failed = deliver_pending(session, LogChannel(), now=NOW)
    assert delivered >= 4 and failed == 0

    with Session(migrated_engine) as session:
        undelivered = session.scalars(select(Alert).where(Alert.delivered_at.is_(None))).all()
        # everything we just created is delivered (LogChannel never fails)
        mine = session.scalars(select(Alert).where(Alert.created_at == NOW)).all()
        assert all(a.delivery_status == "logged" for a in mine)
        assert all(a not in undelivered for a in mine)

    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)


def test_get_alerts_mcp_tool(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)
        ids = _seed(session)
    # Ungated rules (fire-on-everything) — this test asserts all 4 alert types appear; watchlist
    # gating is exercised separately in test_alerts_lifecycle.py (B18).
    run_alerts(migrated_engine, rules=ALL_RULES, channel=LogChannel())  # evaluate + deliver

    server = build_mcp_server(migrated_engine)
    _b, payload = asyncio.run(server.call_tool(TOOL_GET_ALERTS, {"limit": 100}))
    types = {a["alert_type"] for a in payload["alerts"]}
    assert {"sec_filing", "cve_watch", "macro_release", "freshness"}.issubset(types)
    cve = next(a for a in payload["alerts"] if a["alert_type"] == "cve_watch")
    assert cve["evidence_doc_ids"] == [str(ids[CVE_SLUG])]
    assert cve["provenance"]["evidence"][0]["canonical_url"] == "https://nvd.test/CVE-2026-0001"

    # min_severity floor: high+ keeps only the freshness (high) alert among ours.
    _b2, high = asyncio.run(
        server.call_tool(TOOL_GET_ALERTS, {"min_severity": "high", "limit": 100})
    )
    assert all(a["severity"] in ("high", "critical") for a in high["alerts"])
    assert any(a["alert_type"] == "freshness" for a in high["alerts"])

    # alert_types filter.
    _b3, only_sec = asyncio.run(
        server.call_tool(TOOL_GET_ALERTS, {"alert_types": ["sec_filing"], "limit": 100})
    )
    assert {a["alert_type"] for a in only_sec["alerts"]} == {"sec_filing"}

    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)
