<div align="center">

# AtlasCore

**A governed, single-operator data fabric for your own agents and dashboards.**

Turn approved public feeds and official APIs into searchable, cited, provenance-rich evidence your
agents can safely use.

![status](https://img.shields.io/badge/status-pre--soak-2f6fed?style=for-the-badge)
![scope](https://img.shields.io/badge/scope-single--operator-111827?style=for-the-badge)
![stack](https://img.shields.io/badge/stack-PostgreSQL_16-336791?style=for-the-badge)
![mcp](https://img.shields.io/badge/MCP-read--only-0f766e?style=for-the-badge)
![license](https://img.shields.io/badge/license-private-7f1d1d?style=for-the-badge)

</div>

---

AtlasCore collects data from RSS feeds and free/official APIs, stores raw evidence immutably,
normalizes and enriches it, indexes it in Postgres, exposes read-only tools over MCP, and fires
rule-based alerts. It is intentionally small: one operator, one box, one Postgres, one CLI control
plane.

It is **not** a multi-tenant SaaS, **not** a redistribution platform, **not** a social/web crawler,
and **not** an agent-controlled ingestion system.

| | |
|---|---|
| **Package** | `atlascore` |
| **Product name** | AtlasCore, formerly AtlasWire |
| **CLI** | `atlas` |
| **Primary datastore** | PostgreSQL 16 with `pgvector`, `pg_trgm`, FTS, queue tables, and time-series partitions |
| **Runtime shape** | Single-box deployment; Docker Compose for stateful infra; systemd or launchd for scheduler/worker |
| **Current state** | MVP B1-B15 and post-MVP B16-B22 are complete. `main` also includes the first v1 increments. The next operational step is a month-long unattended soak from a pinned clone. |

**Fast path:** [Quickstart](#quickstart) · [Pain points](#pain-points-atlascore-solves) · [How it works](#how-it-works) · [Operator surfaces](#operator-surfaces) · [Status](#current-status)

---

## The promise

Most personal research systems fail in one of two ways: they are either loose piles of scraped text,
or they become overbuilt platforms that need more babysitting than the problems they solve.

AtlasCore takes the middle path:

1. **Only approved sources get fetched.**
2. **Every result cites where it came from.**
3. **Raw evidence is preserved before parsing.**
4. **Agents read processed evidence; they never drive ingestion.**
5. **The whole thing can run unattended on a single box.**

That gives you a private evidence layer for agents, dashboards, alerts, and manual research without
handing your tools a browser fleet, a pile of credentials, or an ungoverned crawler.

---

## Pain points AtlasCore solves

| Pain point | Why it matters | AtlasCore answer |
|---|---|---|
| **Public signal is scattered.** | Central banks, filings, CVEs, releases, papers, and advisories all live in different places. Manual checking does not scale. | A Git-tracked source registry drives scheduled RSS/API connectors for approved sources only. |
| **Agent browsing is too loose.** | Free-form browsing mixes evidence with instructions and makes source policy hard to enforce. | Agents get read-only MCP tools over processed stores. Fetching and policy changes stay in the CLI/control plane. |
| **Search without provenance is weak.** | A snippet is not enough when you need to trust, cite, or debug a result. | Results carry source, URL, document ID, raw payload link, parser version, schema version, and timestamps. |
| **Upstream evidence changes or disappears.** | Feeds roll over, pages change, filings amend, and parser behavior improves. | Raw payloads are stored first in a gzipped, content-addressed raw store; parsed rows remain reproducible from immutable raw. |
| **Public data still has compliance weight.** | Public sources can contain personal data, licensing limits, and attribution requirements. | AtlasCore is internal-only, attribution-first, default-deny, and code-not-data: harvested data and credentials stay out of the repo. |
| **A full data platform is too much.** | Kafka, external search, vector DBs, Kubernetes, and SaaS auth add cost before value for one operator. | One PostgreSQL instance handles relational data, FTS, vectors, queues, audit, and time-series; raw blobs live on local disk. |
| **Jobs must survive retries and crashes.** | One bad item or transient API error should not duplicate data or stop the pipeline. | A Postgres-backed queue uses deterministic dedupe keys, `FOR UPDATE SKIP LOCKED`, backoff, stale-claim reclaim, and a DLQ. |
| **Silent failure is the real outage.** | If the scheduler, worker, sources, delivery, or backups break quietly, the system stops being useful. | Source health, daily digest, liveness checks, optional heartbeat pings, and runbooks make failures visible. |
| **Alerts need evidence, not noise.** | A stream of generic matches becomes another inbox to ignore. | Rules dedupe on stable keys, include evidence document IDs, honor watchlists, and support acknowledge/resolve lifecycle commands. |
| **LLM cost and data egress need hard boundaries.** | Document text may contain personal data, and hosted calls can create surprise spend. | Offline defaults are deterministic; hosted embeddings/summaries are optional, import-guarded, provenance-recorded, and capped by a monthly spend ledger. |
| **Research data has multiple shapes.** | Articles, filings, CVEs, macro series, entities, summaries, and alerts need different query paths. | Dedicated tables model documents, raw payloads, time-series points, entities/mentions, summaries, alerts, source health, and audit logs. |
| **Erasure and backups cannot be bolted on later.** | Internal-only data still needs deletion, purge proof, and recovery. | `atlas purge` performs an audited cascade; retention jobs prune routine rows; backup and restore-drill scripts cover DB plus raw store. |
| **Secrets leak through boring paths.** | API keys in YAML, logs, audit detail, DB dumps, or source files become long-lived incidents. | YAML stores credential refs only; settings use secret types; storage/log sinks redact; optional SOPS/age deploy secrets keep keys outside the repo. |
| **Dashboards are useful, write UIs are risky.** | A browser dashboard can accidentally become an admin panel. | The optional FastAPI/WebUI is read-only, loopback/VPN-oriented, capped, autoescaped, and secret-free. Ingestion management remains CLI-only. |

---

## How it works

```text
Approved source YAML
  -> Scheduler
  -> Postgres crawl_job queue
  -> Fetcher with SSRF guard, rate limits, conditional requests
  -> Immutable raw store
  -> Parser and normalizer
  -> Enrichment: language, entities, topics, embeddings, summaries
  -> Postgres FTS, pgvector, time-series, alerts
  -> CLI, MCP, JSON API, WebUI
```

| Layer | What it does | Why it is shaped this way |
|---|---|---|
| **Source registry** | Git-tracked YAML mirrored into Postgres. | The repo records source intent; the DB records runtime state. Unknown or prohibited sources do not fetch. |
| **Scheduler** | Enqueues due fetches and runs housekeeping cadences. | Exactly one scheduler prevents double-firing and keeps timed work predictable. |
| **Workers** | Drain fetch, parse, and enrich jobs. | Fetch is guarded as a single role; parse/enrich can scale through Postgres row locking. |
| **Raw store** | Stores gzipped, content-addressed payloads on disk. | Evidence is preserved before parser assumptions touch it. |
| **Postgres** | Owns normalized records, FTS, vectors, queue, audit, alerts, health, and time-series. | One durable engine is enough for the single-operator workload. |
| **MCP server** | Exposes seven read-only tools over stdio. | Agents can retrieve cited evidence without controlling ingestion or policy. |
| **CLI** | Runs source management, crawls, health, digest, alerts, backup, purge, and reindex. | Operational authority stays with the operator. |
| **WebUI/API** | Optional read-only dashboard and JSON endpoints. | Browser convenience without adding a write surface. |

**Architecture rule:** MCP, API, WebUI, and agents depend on processed stores only. The ingestion and
control plane must never depend on MCP, and agents never trigger crawls or change source policy.

---

## What you get

| Capability | Outcome |
|---|---|
| **Governed ingestion** | Only approved RSS/API sources are fetched; source policy and allowed use live next to code. |
| **Evidence preservation** | Raw bytes are captured immutably and referenced by every parsed document. |
| **Search that cites** | Postgres full-text search returns sanitized snippets with source and parser provenance. |
| **Entity and topic surfaces** | Mentions, topics, and entity IDs make document retrieval more useful than keyword search alone. |
| **Macro series** | FRED, ECB, and Eurostat-style observations land in partitioned time-series storage. |
| **Summaries with cache discipline** | Summary rows are keyed by content hash, model, version, and prompt so identical inputs are not re-summarized. |
| **Alerts with lifecycle** | CVE, filing, macro, and freshness rules dedupe, cite evidence, deliver, and can be acknowledged or resolved. |
| **Read-only agent tools** | MCP exposes `search_documents`, `get_macro_series`, `get_entity_mentions`, `get_document_summary`, `get_source_health`, `get_alerts`, and `get_raw_document`. |
| **Operational proof** | Health, digest, backup, restore drill, purge audit, retention, and heartbeat support the unattended soak. |

---

## Principles

1. **Internal-only.** Never redistribute harvested data. This is personal research infrastructure.
2. **Code, not data.** Publishable boundaries are connectors, schemas, and code, not captured content or credentials.
3. **Approved RSS and official/free APIs first.** No browser fleet, social-platform ingestion, paywall bypass, proxy rotation, or crawler sprawl.
4. **Evidence beats confidence.** Store provenance, cite records, and keep missing data as `null` instead of inventing certainty.
5. **Small stack, strong edges.** Prefer Postgres, local disk, typed schemas, strict gates, and auditable CLI actions over extra services.

---

## Quickstart

Prerequisites: Python 3.12+, [`uv`](https://github.com/astral-sh/uv), Docker, and Docker Compose.
PostgreSQL 16 with `pgvector` and `pg_trgm` runs in Compose.

```bash
# 1. Clone (private repo for now)
git clone <your-remote>/atlascore.git
cd atlascore

# 2. Install deps + init local env (copies .env.example -> .env)
make setup

# 3. Run Postgres 16 + pgvector
make dev

# 4. Apply migrations
make migrate

# 5. Validate and sync the source registry
make seed
```

Integration and smoke tests need a reachable Postgres 16. They skip cleanly when none is present, so
`make test` stays useful offline for unit and contract coverage.

---

## Operator surfaces

### CLI control plane

`atlas` is the only ingestion-management surface.

```bash
atlas source validate --all          # validate source YAML
atlas source sync --all              # mirror approved sources into Postgres
atlas source test <source_id>        # dry-run policy + connector checks
atlas crawl once <source_id>         # enqueue/run one manual fetch
atlas crawl dlq                      # inspect dead jobs
atlas crawl requeue <job_id>         # recover a dead job
atlas parse <raw_id>                 # re-parse immutable raw
atlas enrich <doc_id>                # run enrichment for one document
atlas search "query terms"           # full-text search with provenance
atlas alerts run                     # evaluate and deliver alerts
atlas alerts ack <alert_id>          # acknowledge an alert
atlas health                         # source health + DLQ + liveness
atlas digest                         # daily digest via delivery channel
atlas backup                         # local pg_dump + raw-store copy
atlas purge <doc_id|entity_id>       # audited erasure cascade
atlas reindex-embeddings             # bounded embedding backfill/reindex
atlas scheduler run                  # exactly-one scheduler loop
atlas worker run                     # continuous fetch/parse/enrich drain loop
atlas mcp dev                        # read-only MCP server over stdio
```

### Read-only WebUI and JSON API

Install the web extra and bind to loopback:

```bash
uv sync --extra web
uvicorn atlascore.api.app:app --host 127.0.0.1 --port 8000
```

Routes:

| Surface | Path |
|---|---|
| Browser dashboard | `/`, `/search`, `/documents/{id}`, `/alerts`, `/sources`, `/macro` |
| JSON API | `/v1/search`, `/v1/alerts`, `/v1/sources/health` |
| Liveness | `/healthz` |

No auth is built in. Keep it on loopback or put it behind a VPN/static-token front door if exposed.

---

## Quality gates

```bash
make format        # ruff format
make lint          # ruff lint
make typecheck     # mypy
make test          # unit + contract + integration when DB is reachable
make security      # SSRF, sanitizer, secrets, prompt-injection regressions
make smoke         # source -> ingest -> enrich -> search -> MCP summary -> alert -> backup -> purge
make audit         # dependency vulnerability audit
make precommit     # all local hooks
```

CI mirrors the same posture: formatting, linting, strict typing, unit/contract tests, migration
checks, source YAML validation, secrets scanning, dependency audit, and optional Postgres integration.

---

## Repository map

```text
atlascore/
  README.md  CLAUDE.md  CHANGELOG.md  CONTRIBUTING.md  SECURITY.md  LICENSE
  .env.example  .pre-commit-config.yaml  pyproject.toml  uv.lock  Makefile
  .github/        workflows/ci.yml, dependabot.yml
  sources/        Git-tracked source registry YAML
  watchlist.yaml  Governed alert watchlist
  src/atlascore/
    core/              settings, logging, IDs, clock, errors
    schemas/           Pydantic models and canonical shapes
    connectors_sdk/    connector interface, fetcher, rate limit
    connectors/        RSS, FRED, SEC, NVD, ECB, Eurostat, GitHub, arXiv
    parsers/           article, filing, macro parsers
    enrichment/        language, NER, topics, embeddings, summaries
    storage/           Postgres stores, raw store, queue, search, purge
    security/          SSRF guard, sanitization, secrets, sandboxing
    alerts/            rules and delivery channels
    cli/               Typer app; `atlas`
    scheduler/         single-instance scheduler
    worker/            fetch/parse/enrich loops
    mcp_server/        read-only MCP tools
    api/               optional read-only WebUI + JSON API
  migrations/          Alembic migrations
  infra/               Compose, backup, systemd, launchd, restore drill
  tests/               unit, integration, fixtures
  docs/                architecture, connectors, schemas, security, compliance, MCP, runbooks, ADRs
```

Rule: do not add top-level directories without a clear reason.

---

## Documentation

| Document | Purpose |
|---|---|
| [`CLAUDE.md`](CLAUDE.md) | Agent operating contract: identity, boundaries, rituals, architecture, MCP tools, security, compliance, and definition of done. |
| [`docs/STATUS.md`](docs/STATUS.md) | Living board: current phase, done, next, known issues, and coordination protocol. |
| [`docs/PROJECT-PLAN.md`](docs/PROJECT-PLAN.md) | Point-in-time rationale, roadmap, MVP backlog, source categories, risks, and cost model. |
| [`docs/architecture/README.md`](docs/architecture/README.md) | Components, data flow, concurrency, degradation, and control-plane boundaries. |
| [`docs/connectors/README.md`](docs/connectors/README.md) | Connector lifecycle and canonical source YAML. |
| [`docs/schemas/README.md`](docs/schemas/README.md) | Canonical tables and schema design resolutions. |
| [`docs/security/README.md`](docs/security/README.md) | SSRF, URL safety, sanitization, secrets, sandboxing, and prompt-injection posture. |
| [`docs/compliance/README.md`](docs/compliance/README.md) | Internal-only posture, PII framing, retention, attribution, and code-not-data boundary. |
| [`docs/mcp/README.md`](docs/mcp/README.md) | The seven read-oriented MCP tools and their contracts. |
| [`docs/runbooks/README.md`](docs/runbooks/README.md) | Deploy, backup/restore, purge, backfill/reindex, secrets, and recovery procedures. |
| [`docs/decisions/`](docs/decisions/) | ADRs `0000` through `0037`. |

---

## Current status

MVP **B1-B15** and the post-MVP cycle **B16-B22** are complete. `main` also includes the first v1
increments: ECB, arXiv, GitHub releases/advisories, Eurostat, optional SOPS/age deploy secrets, DLQ
requeue, and the read-only JSON API + server-rendered WebUI.

The full path is implemented: approved source -> ingest -> immutable raw -> parse -> enrich -> search
-> MCP summary -> alert -> backup -> purge. `make smoke` proves the chain when Postgres is available;
`make security` runs the hardening suite.

Scope discipline still matters. The live-board plan keeps the unattended soak isolated in a pinned
clone while v1 work lands on `main`. The next operational step is to configure the box, set delivery
and heartbeat, prove backups restore, deploy one scheduler and one worker, and let the soak run.

---

## License

Private repository, all rights reserved. The `LICENSE` file is a proprietary placeholder while the
repo is private. The intended public boundary is **code, not data**: publish connectors, schemas, and
code later, but never harvested data or private credentials.
