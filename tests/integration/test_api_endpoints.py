"""Integration: the read-only dashboard API endpoints over a real DB (needs the `web` extra)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session

from atlascore.api.app import create_app
from atlascore.core.ids import new_id
from atlascore.schemas.document import DocumentRecord
from atlascore.storage.documents import upsert_document
from atlascore.storage.postgres.models import (
    Alert,
    Document,
    RawDocument,
    Source,
    SourceHealth,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)
SLUG = "api_demo"


def _cleanup(session: Session) -> None:
    session.execute(delete(Alert).where(Alert.dedupe_key.like(f"{SLUG}%")))
    ids = list(
        session.scalars(select(Source.id).where(Source.config["slug"].astext.like(f"{SLUG}%")))
    )
    if ids:
        session.execute(delete(SourceHealth).where(SourceHealth.source_id.in_(ids)))
        session.execute(delete(Document).where(Document.source_id.in_(ids)))
        session.execute(delete(RawDocument).where(RawDocument.source_id.in_(ids)))
        session.execute(delete(Source).where(Source.id.in_(ids)))


def _seed(session: Session) -> None:
    source = Source(
        name=f"{SLUG}-src",
        category="news_rss",
        access_method="rss",
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
    upsert_document(
        session,
        DocumentRecord(
            source_id=source.id,
            raw_document_id=raw.id,
            canonical_url=f"https://{SLUG}.test/x",
            title="Quantum widget breakthrough",
            body_text="A breakthrough in quantum widget manufacturing was announced today.",
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
            dedupe_key=f"{SLUG}:demo",
            evidence_doc_ids=[],
            detail={},
        )
    )


def test_dashboard_endpoints(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)
        _seed(session)

    client = TestClient(create_app(engine=migrated_engine))
    try:
        # search
        r = client.get("/v1/search", params={"q": "quantum widget", "source": SLUG})
        assert r.status_code == 200
        body = r.json()
        assert body["count"] >= 1
        assert any("quantum" in (h["snippet"] or "").lower() for h in body["results"])
        assert body["results"][0]["source_slug"] == SLUG  # provenance present

        # alerts
        r = client.get("/v1/alerts", params={"type": "cve_watch", "min_severity": "high"})
        assert r.status_code == 200
        assert any(a["title"] == "Demo alert" for a in r.json()["alerts"])

        # source health (no secrets in the payload)
        r = client.get("/v1/sources/health", params={"status": "warn"})
        assert r.status_code == 200
        sources = r.json()["sources"]
        assert any(s["source_slug"] == SLUG and s["status"] == "warn" for s in sources)
        assert all("base_url" not in s and "credential_ref" not in s for s in sources)
    finally:
        client.close()
        with Session(migrated_engine) as session, session.begin():
            _cleanup(session)
