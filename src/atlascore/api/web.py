"""Server-rendered, **read-only** WebUI for AtlasCore (v1; needs the `web` extra).

A small Jinja2 + plain-HTML browser UI over the processed stores — dashboard, full-text search +
document detail, alerts, source health, and the macro-series browser. **No JavaScript build step**
(zero JS; an inline server-rendered SVG sparkline is the only "chart"), so it works offline, is
accessible, and has a minimal attack surface.

Posture (same as the JSON API + MCP, CLAUDE.md §6/§13/§17): **read-only** — it renders processed
data and never drives the control plane (no triggering crawls, no policy/alert writes; those stay on
the CLI). No auth by design (single-operator; bind to loopback / front with a VPN if exposed). All
external content is **untrusted evidence**: every template value is HTML-escaped by Jinja autoescape,
and search snippets are escaped *before* their `«…»` match markers are turned into `<mark>` tags.
Never renders `base_url` / `credential_ref` / internal hostnames.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, FastAPI, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape
from sqlalchemy import func, select

from atlascore.api.app import SessionDep
from atlascore.storage.alerts import list_alerts
from atlascore.storage.documents import get_document_view
from atlascore.storage.postgres.models import Document
from atlascore.storage.search import search_documents
from atlascore.storage.source_health import list_source_health
from atlascore.storage.timeseries import list_series, read_macro_series

_HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(_HERE / "templates"))

UI_SEARCH_LIMIT = 50
UI_ALERTS_LIMIT = 100
UI_MACRO_LIMIT = 2000
_HEALTH_ORDER = ["error", "stale", "cooling_down", "warn", "ok"]


def _highlight(snippet: str | None) -> Markup:
    """Render a search snippet: HTML-escape the untrusted text FIRST, then turn the `«…»` match
    markers (literal, non-HTML chars chosen by the FTS layer) into `<mark>` — never the reverse."""
    if not snippet:
        return Markup("")
    safe = str(escape(snippet))  # untrusted → escaped (no raw HTML can survive)
    safe = safe.replace("«", "<mark>").replace("»", "</mark>")
    # Safe by construction: the snippet was HTML-escaped first, so the ONLY markup is the <mark>
    # tags we inject for the FTS «…» delimiters. Asserted by test_highlight_escapes_untrusted_then_marks.
    return Markup(safe)  # noqa: S704


def _dt(value: datetime | None) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M") if value else "—"


def _lag(value: Any) -> str:
    """Compact freshness-lag rendering (e.g. ``3d22h``) for a ``timedelta`` or None."""
    if value is None:
        return "—"
    total = int(value.total_seconds())
    sign = "-" if total < 0 else ""
    total = abs(total)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{sign}{days}d{hours}h"
    if hours:
        return f"{sign}{hours}h{minutes}m"
    return f"{sign}{minutes}m"


def _sparkline(points: list[Any], *, width: int = 600, height: int = 80) -> Markup:
    """A dependency-free inline SVG line of the series' values (server-rendered; numeric-only)."""
    vals = [float(p.value) for p in points if p.value is not None]
    if len(vals) < 2:
        return Markup("")
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1.0
    n = len(vals)
    coords = " ".join(
        f"{i / (n - 1) * width:.1f},{height - (v - lo) / rng * (height - 4) - 2:.1f}"
        for i, v in enumerate(vals)
    )
    # Safe by construction: width/height are int constants and every coordinate is a formatted
    # float — no user/external string enters the SVG. Asserted by test_sparkline_is_numeric_only_svg.
    return Markup(  # noqa: S704
        f'<svg class="spark" viewBox="0 0 {width} {height}" preserveAspectRatio="none" '
        f'role="img" aria-label="series sparkline">'
        f'<polyline fill="none" stroke="currentColor" stroke-width="1.5" points="{coords}"/></svg>'
    )


def _safe_url(url: str | None) -> str | None:
    """Render-time scheme allowlist for hrefs: only http(s) URLs are emitted. HTML autoescape does
    NOT neutralize ``javascript:``/``data:`` schemes, so the UI gates untrusted URLs (canonical_url)
    here — defense in depth (the article parser already strips non-http(s), but a future connector
    might not). Returns None when the scheme isn't allowed, so the template renders no link."""
    if url and url.strip().lower().startswith(("http://", "https://")):
        return url
    return None


templates.env.filters["highlight"] = _highlight
templates.env.filters["dt"] = _dt
templates.env.filters["lag"] = _lag
templates.env.filters["safe_url"] = _safe_url

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: SessionDep) -> HTMLResponse:
    health = list_source_health(session)
    counts: dict[str, int] = dict.fromkeys(_HEALTH_ORDER, 0)
    for row in health:
        counts[row.status] = counts.get(row.status, 0) + 1
    doc_count = session.scalar(select(func.count()).select_from(Document)) or 0
    recent_alerts = list_alerts(session, limit=8)
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "health": health,
            "counts": counts,
            "health_order": _HEALTH_ORDER,
            "doc_count": doc_count,
            "recent_alerts": recent_alerts,
        },
    )


@router.get("/search", response_class=HTMLResponse)
def search(
    request: Request,
    session: SessionDep,
    q: str | None = None,
    source: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> HTMLResponse:
    # UI date inputs are date-only (midnight); make `until` inclusive of the chosen day. The form
    # echoes the original `until` so the date picker still shows what the operator selected.
    until_q = until + timedelta(days=1) if until else None
    hits = (
        search_documents(
            session, q, source=source or None, since=since, until=until_q, limit=UI_SEARCH_LIMIT
        )
        if q
        else []
    )
    return templates.TemplateResponse(
        request,
        "search.html",
        {"q": q or "", "source": source or "", "since": since, "until": until, "hits": hits},
    )


@router.get("/documents/{document_id}", response_class=HTMLResponse)
def document_detail(request: Request, session: SessionDep, document_id: str) -> HTMLResponse:
    # Parse the id here (not via a UUID path type) so a non-UUID renders the friendly 404 page
    # instead of FastAPI's raw 422 JSON — this is a human-facing browser surface.
    try:
        doc_uuid: UUID | None = UUID(document_id)
    except ValueError:
        doc_uuid = None
    doc = get_document_view(session, doc_uuid) if doc_uuid is not None else None
    status = 200 if doc is not None else 404
    return templates.TemplateResponse(
        request, "document.html", {"doc": doc, "document_id": document_id}, status_code=status
    )


@router.get("/alerts", response_class=HTMLResponse)
def alerts(
    request: Request,
    session: SessionDep,
    type: str | None = None,
    status: str | None = None,
    min_severity: str | None = None,
) -> HTMLResponse:
    # NOTE: AlertRow carries `detail` (free-form jsonb, the §14 secret-risk column class). It is
    # intentionally NEVER rendered by alerts.html / index.html — do not add `{{ a.detail }}`.
    rows = list_alerts(
        session,
        alert_types=[type] if type else None,
        statuses=[status] if status else None,
        min_severity=min_severity or None,
        limit=UI_ALERTS_LIMIT,
    )
    return templates.TemplateResponse(
        request,
        "alerts.html",
        {
            "alerts": rows,
            "type": type or "",
            "status": status or "",
            "min_severity": min_severity or "",
        },
    )


@router.get("/sources", response_class=HTMLResponse)
def sources(request: Request, session: SessionDep, status: str | None = None) -> HTMLResponse:
    rows = list_source_health(session, statuses=[status] if status else None)
    return templates.TemplateResponse(
        request, "sources.html", {"sources": rows, "status": status or ""}
    )


@router.get("/macro", response_class=HTMLResponse)
def macro_index(request: Request, session: SessionDep) -> HTMLResponse:
    return templates.TemplateResponse(request, "macro.html", {"series": list_series(session)})


@router.get("/macro/series", response_class=HTMLResponse)
def macro_series(
    request: Request,
    session: SessionDep,
    provider: str = Query(min_length=1),
    series_id: str = Query(min_length=1),
) -> HTMLResponse:
    series = read_macro_series(
        session, provider=provider, series_id=series_id, limit=UI_MACRO_LIMIT
    )
    spark = _sparkline(series.points) if series is not None else Markup("")
    return templates.TemplateResponse(
        request,
        "macro_series.html",
        {"provider": provider, "series_id": series_id, "series": series, "spark": spark},
    )


def register_web_ui(app: FastAPI) -> None:
    """Mount the static dir + include the read-only HTML UI routes on ``app`` (called by create_app)."""
    app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")
    app.include_router(router)
