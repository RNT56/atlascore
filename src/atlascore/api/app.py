"""Optional thin **read-only** dashboard API over processed AtlasCore data (v1, CLAUDE.md §7, §15.2).

A small FastAPI surface that exposes the same processed stores the MCP read tools do — full-text
search, alerts, and source health — over HTTP, for a browser dashboard. It is **read-only**: no
ingestion control, no writes, no secrets (never returns `base_url`/`credential_ref`/internal
endpoints). Like the MCP server, it depends only on the processed stores; the ingestion/control
plane never depends on it (§6 architecture rule). Results are capped and carry provenance.

Run it (needs the `web` extra — ``uv sync --extra web``):

    uvicorn atlascore.api.app:app --host 127.0.0.1 --port 8000

Bind to loopback / put it behind Tailscale or a static token if ever exposed (§13) — there is no
auth here by design (single-operator box).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Query, Request
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from atlascore.storage.alerts import AlertRow, list_alerts
from atlascore.storage.postgres.base import make_engine
from atlascore.storage.search import SearchHit, search_documents
from atlascore.storage.source_health import SourceHealthRow, list_source_health

# Hard cap on any list endpoint (mirrors the MCP result caps, §13).
API_MAX_LIMIT = 100


def _get_session(request: Request) -> Iterator[Session]:
    """Per-request session bound to the app's engine (set on ``app.state`` in :func:`create_app`).

    Module-level (not a closure) so FastAPI can resolve the ``Depends`` in the stringized
    annotations produced by ``from __future__ import annotations`` — a closure-local dependency
    would be invisible to ``get_type_hints`` and silently demoted to a query param.
    """
    engine: Engine = request.app.state.engine
    with Session(engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(_get_session)]


def _hit_json(h: SearchHit) -> dict[str, Any]:
    return {
        "document_id": str(h.document_id),
        "source_slug": h.source_slug,
        "source_name": h.source_name,
        "canonical_url": h.canonical_url,
        "title": h.title,
        "language": h.language,
        "topics": h.topics,
        "published_at": h.published_at.isoformat() if h.published_at else None,
        "rank": h.rank,
        "snippet": h.snippet,
    }


def _alert_json(a: AlertRow) -> dict[str, Any]:
    return {
        "alert_id": str(a.alert_id),
        "alert_type": a.alert_type,
        "severity": a.severity,
        "status": a.status,
        "title": a.title,
        "created_at": a.created_at.isoformat(),
        "evidence": [
            {
                "document_id": str(e.document_id),
                "source_name": e.source_name,
                "canonical_url": e.canonical_url,
            }
            for e in a.evidence
        ],
    }


def _health_json(r: SourceHealthRow) -> dict[str, Any]:
    return {
        "source_slug": r.source_slug,
        "source_name": r.source_name,
        "status": r.status,
        "freshness_lag_seconds": (
            r.freshness_lag.total_seconds() if r.freshness_lag is not None else None
        ),
        "consecutive_failures": r.consecutive_failures,
        "last_success_at": r.last_success_at.isoformat() if r.last_success_at else None,
        "item_count_recent": r.item_count_recent,
    }


def create_app(engine: Engine | None = None) -> FastAPI:
    """Build the read-only dashboard app. ``engine`` is injectable for tests; else ``make_engine()``.

    The engine is created lazily-connecting (no network at import/startup); a DB outage surfaces as a
    per-request 500, not a startup failure — intended for a single-operator box (mirrors the MCP server).
    """
    app = FastAPI(
        title="AtlasCore",
        version="0.1",
        description="Read-only dashboard API over processed AtlasCore data.",
    )
    app.state.engine = engine or make_engine()  # read per-request by the module-level _get_session

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        """Liveness probe (no DB)."""
        return {"status": "ok"}

    @app.get("/v1/search")
    def search(
        session: SessionDep,
        q: Annotated[str, Query(min_length=1, description="Full-text query")],
        source: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: Annotated[int, Query(ge=1, le=API_MAX_LIMIT)] = 20,
    ) -> dict[str, Any]:
        hits = search_documents(
            session, q, source=source, since=since, until=until, limit=min(limit, API_MAX_LIMIT)
        )
        return {"query": q, "count": len(hits), "results": [_hit_json(h) for h in hits]}

    @app.get("/v1/alerts")
    def alerts(
        session: SessionDep,
        type: Annotated[list[str] | None, Query()] = None,
        status: Annotated[list[str] | None, Query()] = None,
        min_severity: str | None = None,
        since: datetime | None = None,
        limit: Annotated[int, Query(ge=1, le=API_MAX_LIMIT)] = 50,
    ) -> dict[str, Any]:
        rows = list_alerts(
            session,
            alert_types=type,
            statuses=status,
            min_severity=min_severity,
            since=since,
            limit=min(limit, API_MAX_LIMIT),
        )
        return {"count": len(rows), "alerts": [_alert_json(a) for a in rows]}

    @app.get("/v1/sources/health")
    def sources_health(
        session: SessionDep,
        status: Annotated[list[str] | None, Query()] = None,
    ) -> dict[str, Any]:
        rows = list_source_health(session, statuses=status)
        return {"count": len(rows), "sources": [_health_json(r) for r in rows]}

    # Mount the read-only server-rendered WebUI (HTML at /, static at /static). Local import keeps
    # the web module (which imports SessionDep from here) from creating an import cycle at load time.
    from atlascore.api.web import register_web_ui

    register_web_ui(app)
    return app


# Module-level app for `uvicorn atlascore.api.app:app` (engine created lazily; no network at import).
app = create_app()
