"""Integration: the read-only WebUI pages render over a real DB (needs the `web` extra).

Covers each page end-to-end and asserts the two load-bearing invariants: untrusted content is
HTML-escaped (no XSS), and the source-health page leaks no secrets (no base_url / credential_ref).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jinja2")

from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session

from atlascore.api.app import create_app
from atlascore.connectors_sdk.types import Observation, ParsedSeries
from atlascore.core.ids import new_id
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
from atlascore.storage.timeseries import upsert_observations

pytestmark = pytest.mark.integration

NOW = datetime(2026, 6, 8, 12, 0, tzinfo=UTC)
SLUG = "webui_demo"
XSS_TITLE = "<script>alert('xss')</script> Quantum widget"
XSS_BODY = "Quantum breakthrough. Evil: <img src=x onerror=alert(1)> and <b>bold</b>."
PROVIDER = "webuitest"
SERIES_ID = "DEMO_SERIES"


def _cleanup(session: Session) -> None:
    session.execute(delete(Alert).where(Alert.dedupe_key.like(f"{SLUG}%")))
    session.execute(delete(TimeSeriesPoint).where(TimeSeriesPoint.provider == PROVIDER))
    ids = list(
        session.scalars(select(Source.id).where(Source.config["slug"].astext.like(f"{SLUG}%")))
    )
    if ids:
        session.execute(delete(SourceHealth).where(SourceHealth.source_id.in_(ids)))
        session.execute(delete(Document).where(Document.source_id.in_(ids)))
        session.execute(delete(RawDocument).where(RawDocument.source_id.in_(ids)))
        session.execute(delete(Source).where(Source.id.in_(ids)))


def _seed(session: Session) -> str:
    source = Source(
        name=f"{SLUG}-src",
        category="news_rss",
        access_method="rss",
        base_url="https://secret-internal-host.example/private-feed",  # must NOT leak to the UI
        policy_status="approved",
        enabled=True,
        config={"slug": SLUG},
        schema_version=1,
    )
    session.add(source)
    session.flush()
    raw = RawDocument(
        source_id=source.id, content_hash=f"sha256:{new_id().hex}", path="a/b.gz", schema_version=1
    )
    session.add(raw)
    session.flush()
    result = upsert_document(
        session,
        DocumentRecord(
            source_id=source.id,
            raw_document_id=raw.id,
            canonical_url=f"https://{SLUG}.test/x",
            title=XSS_TITLE,
            body_text=XSS_BODY,
            published_at=NOW,
            content_hash=f"sha256:{SLUG}",
            parser_version="article@1.0.0",
        ),
        now=NOW,
    )
    session.add(
        SourceHealth(source_id=source.id, status="warn", consecutive_failures=2, updated_at=NOW)
    )
    session.add(
        Alert(
            alert_type="cve_watch",
            rule_id="cve_watch",
            severity="high",
            status="open",
            title="Demo alert",
            dedupe_key=f"{SLUG}:a",
            evidence_doc_ids=[str(result.document_id)],
            detail={},
        )
    )
    upsert_observations(
        session,
        ParsedSeries(
            provider=PROVIDER,
            series_id=SERIES_ID,
            unit="index",
            observations=[
                Observation(ts=datetime(2025, 1, 1, tzinfo=UTC), value=Decimal("1.0")),
                Observation(ts=datetime(2025, 2, 1, tzinfo=UTC), value=Decimal("2.5")),
                Observation(ts=datetime(2025, 3, 1, tzinfo=UTC), value=None),  # None-value point
            ],
        ),
    )
    # An edge document inserted DIRECTLY via the ORM (bypassing the DocumentRecord validator) with
    # None title/body/published (None template paths) AND a javascript: canonical_url — to prove the
    # template's safe_url gate drops dangerous-scheme hrefs even if such a value reached the DB.
    edge = Document(
        source_id=source.id,
        raw_document_id=raw.id,
        canonical_url="javascript:alert(document.domain)",
        title=None,
        body_text=None,
        published_at=None,
        ingested_at=NOW,
        topics=[],
        entity_ids=[],
        content_hash=f"sha256:{SLUG}-edge",
        parser_version="article@1.0.0",
        schema_version=1,
    )
    session.add(edge)
    session.flush()
    return str(result.document_id), str(edge.id)


def test_webui_pages(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)
        doc_id, edge_id = _seed(session)

    client = TestClient(create_app(engine=migrated_engine))
    try:
        # dashboard
        r = client.get("/")
        assert r.status_code == 200 and "Dashboard" in r.text

        # search — hit present, and the XSS title is ESCAPED (no raw <script>)
        r = client.get("/search", params={"q": "quantum", "source": SLUG})
        assert r.status_code == 200
        assert "Quantum widget" in r.text
        assert "<script>alert('xss')</script>" not in r.text
        assert "&lt;script&gt;" in r.text
        assert "<mark>" in r.text  # snippet match markers became <mark>

        # document detail — body shown, XSS escaped (no raw <script>/<img onerror>)
        r = client.get(f"/documents/{doc_id}")
        assert r.status_code == 200
        assert "<script>alert('xss')</script>" not in r.text
        assert "<img src=x onerror=alert(1)>" not in r.text
        assert "&lt;img" in r.text  # body rendered, escaped

        # unknown document → 404; a NON-UUID id → friendly 404 page (not FastAPI's raw 422)
        assert client.get(f"/documents/{new_id()}").status_code == 404
        assert client.get("/documents/not-a-uuid").status_code == 404

        # edge doc: None title/body/published render fine, and the javascript: canonical_url is NOT
        # emitted as an href (safe_url dropped it) — the template defends even if the validator didn't.
        r = client.get(f"/documents/{edge_id}")
        assert r.status_code == 200
        assert 'href="javascript:' not in r.text
        assert "(untitled)" in r.text  # None title falls back, page still renders

        # alerts
        r = client.get("/alerts")
        assert r.status_code == 200 and "Demo alert" in r.text

        # sources — source shown, and NO secret base_url leaks into the page
        r = client.get("/sources")
        assert r.status_code == 200 and SLUG in r.text
        assert "secret-internal-host.example" not in r.text

        # macro browser + a single series (sparkline + points)
        r = client.get("/macro")
        assert r.status_code == 200 and SERIES_ID in r.text
        r = client.get("/macro/series", params={"provider": PROVIDER, "series_id": SERIES_ID})
        assert r.status_code == 200
        assert "<polyline" in r.text and "2.5" in r.text
    finally:
        client.close()
        with Session(migrated_engine) as session, session.begin():
            _cleanup(session)
