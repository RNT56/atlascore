# 0037. V1: a server-rendered, read-only WebUI (`atlascore.api.web`)

- **Status:** Accepted
- **Date:** 2026-06-08
- **Deciders:** Operator (single-operator project)
- **Spec reference:** AtlasCore Canonical Decisions Spec v1 (date 2026-06-03), §7 (`api/` optional read surface), §13 (read-only posture, no secrets, result caps), §6/§17 (control plane must not depend on read surfaces), §18 (boring/maintainable). Builds on [ADR 0035](0035-v1-read-only-fastapi-dashboard.md), which shipped the JSON API and explicitly deferred a real HTML frontend.

## Context

ADR 0035 added the read-only JSON API (`/v1/*`) but left "a real HTML/JS frontend out of scope." The operator now wants an actual browser UI to browse the processed fabric (search, documents, alerts, source health, macro series). The question is *how* to build it without violating the architecture or dragging a heavy frontend toolchain into a lean, single-operator Python project.

## Decision

Add `atlascore.api.web` — a **server-rendered, read-only** WebUI mounted on the existing `atlascore.api` app (HTML at `/`, `/search`, `/documents/{id}`, `/alerts`, `/sources`, `/macro`, `/macro/series`; the JSON API stays at `/v1/*`; static at `/static`). It ships behind the existing **`web`** extra (adds `jinja2`).

Stack choices, all justified by the project's constraints:

- **Jinja2 + plain HTML, ZERO JavaScript.** No SPA, no npm, no build step. Forms submit via GET and pages re-render; the only "chart" is a server-rendered inline **SVG sparkline** (numeric-only). This is the most robust, accessible, offline-friendly, minimal-attack-surface choice for a single-operator internal tool (§18 boring/maintainable), and it avoids a Node toolchain in a Python repo.
- **Read-only, enforced (§6/§13/§17).** Every route is a GET that only *reads* processed stores via the same query functions the CLI/MCP use (`search_documents`, `get_document_view`, `list_alerts`, `list_source_health`, `list_series`, `read_macro_series`). The UI never triggers crawls, changes policy, or writes/acks alerts — those stay on the CLI. It does not import the worker/scheduler/queue-write paths.
- **Untrusted content is escaped.** All external text (titles, bodies, snippets, names) is rendered through Jinja **autoescape** (on for `.html`). Search snippets are HTML-escaped *first*, then the FTS `«…»` match markers are turned into `<mark>` (a custom `highlight` filter; the only two `Markup` uses — `highlight` and the numeric sparkline — are safe by construction, `# noqa: S704`, and asserted by tests). Outbound `canonical_url` links carry `rel="noopener noreferrer nofollow" target="_blank"`.
- **No secrets.** Pages render the same secret-free views as the JSON/MCP surfaces — never `base_url`, `credential_ref`, internal hostnames, or free-form `last_error`/`detail`.
- **No auth** by design (single-operator; bind to loopback / front with a VPN if exposed, §13). The engine connects lazily, so a DB outage is a per-request 500, not a startup failure.

Run it: `uvicorn atlascore.api.app:app --host 127.0.0.1 --port 8000` (now serves both the UI and the JSON API).

## Consequences

- **Positive:** a usable browser dashboard over the whole processed fabric with no frontend build, no new runtime service (it's the same app as the JSON API), one source of truth for queries (CLI/MCP/JSON/UI all call the same storage functions), and a tiny, escape-by-default attack surface. Unit tests cover the security-critical escaping + no-DB render; an integration test renders every page over a real DB and asserts XSS-escaping + no secret leak.
- **Negative / costs:** server round-trip per interaction (no client-side reactivity — acceptable for a single operator); `jinja2` added to the `web` extra; templates/static are package data (shipped by hatchling's default file inclusion; resolved at runtime via `Path(__file__).parent`).
- **Follow-ups / obligations:** record the deploy posture (loopback/VPN) in the runbook + a serve note; a static-token/VPN front before any exposed deployment; optional later: entity-mentions page, summaries view, pagination. Write actions (e.g. alert ack) would require explicit human-confirmation semantics + a new ADR (not built).
- **Revisit triggers:** exposing the UI beyond loopback/VPN (add auth); wanting client-side interactivity at a scale that justifies a JS layer (reconsider HTMX or a small SPA, with an ADR).
