# 0035. V1 read-only FastAPI dashboard API (`atlascore.api`)

- **Status:** Accepted
- **Date:** 2026-06-07
- **Deciders:** Operator (single-operator project)
- **Spec reference:** AtlasCore Canonical Decisions Spec v1 (date 2026-06-03), §7 (`api/` is an OPTIONAL thin read-only FastAPI surface, `[app]`), §13 (read-only posture, result caps, no secrets), §6 (control plane must not depend on read surfaces), roadmap §15.2 (v1)

## Context

V1 wants an optional browser dashboard over the processed data. The MCP server already exposes the processed stores (search, alerts, source health) to agents over stdio; a human-facing dashboard needs the same data over HTTP. The spec allows exactly one such surface — a **thin, read-only FastAPI app** — and only if a dashboard is wanted (it is). It must not become a control plane, must not leak secrets, and must not be a dependency of ingestion.

This is built during the soak as a parallel-track v1 increment: it lands on `main` but is **not** deployed into the pinned soak instance until the month completes (§5).

## Decision

Add `src/atlascore/api/app.py` — a `create_app(engine=None) -> FastAPI` factory plus a module-level `app = create_app()` for `uvicorn atlascore.api.app:app`. It ships behind the existing **`web`** optional extra (`fastapi`, `uvicorn`); CI installs it via `uv sync --all-extras`, so mypy + the dashboard tests run in CI.

Endpoints (all **read-only**, all capped at `API_MAX_LIMIT=100`, all returning provenance, none returning `base_url`/`credential_ref`/internal endpoints):

- `GET /healthz` — liveness (no DB).
- `GET /v1/search?q=&source=&since=&until=&limit=` — reuses `storage.search.search_documents`.
- `GET /v1/alerts?type=&status=&min_severity=&since=&limit=` — reuses `storage.alerts.list_alerts`.
- `GET /v1/sources/health?status=` — reuses `storage.source_health.list_source_health` (secret-free view).

The per-request DB session is a **module-level** dependency (`_get_session(request)`) that reads the engine from `app.state.engine` (set in `create_app`). This is deliberate: with `from __future__ import annotations`, endpoint annotations are strings resolved by `get_type_hints` against **module** globals, so a closure-local `Depends(...)` is invisible and FastAPI silently demotes the session parameter to a required query param (a 422). The module-level dependency + `app.state` engine avoids that and keeps the engine injectable for tests.

Posture: no auth by design (single-operator box) — bind to loopback / front with Tailscale or a static token if ever exposed (§13). The engine connects lazily, so a DB outage is a per-request 500, not a startup failure (mirrors the MCP server). The ingestion/control plane does not import `atlascore.api` (§6).

## Consequences

- **Positive:** a browser dashboard can read search/alerts/health over HTTP, reusing the exact query functions the CLI/MCP use (one source of truth, consistent caps + provenance). Optional (`web` extra) so the core install stays lean. Tested at both layers (unit: healthz/validation no-DB; integration: all three endpoints over a real DB, asserting no secrets leak).
- **Negative / costs:** a second read surface to keep in lockstep with the storage query signatures; no auth (acceptable only for a single-operator/loopback or VPN deployment); FastAPI's TestClient emits a Starlette/httpx deprecation warning, suppressed via a targeted `filterwarnings` ignore so warnings-as-errors stays strict elsewhere.
- **Follow-ups / obligations:** a static-token/VPN front before any exposed deployment; optional write tools (e.g. alert ack/resolve) would require explicit human-confirmation semantics and a new ADR (not built); a real HTML/JS frontend is out of scope (this is the JSON API only).
- **Revisit triggers:** exposing the API beyond loopback/VPN (add auth); wanting any write endpoint (new ADR); the read surface diverging from MCP (consider sharing a query layer).
