# AtlasCore

> A governed, single-operator data fabric for your own agents and dashboards.

AtlasCore is a small, governed ingestion-and-retrieval system for **one operator**. It collects data from RSS feeds and free/official APIs, preserves raw evidence, normalizes and lightly enriches it, makes it searchable, exposes read-only tools to your agents over MCP, and fires a few rule-based alerts. It runs unattended on a single box.

It is **not** a multi-tenant SaaS, **not** a redistribution platform, and **not** a general web crawler.

- **Project / repository / Python distribution name:** `atlascore`
- **Product / brand name:** AtlasCore *(formerly "AtlasWire" — fully renamed)*
- **CLI command:** `atlas`
- **Status:** **MVP complete (B1–B15).** Source → ingest → enrich → search → MCP summary → alert runs unattended, with backups + an audited erasure path; `make smoke` proves it end to end and `make security` runs the hardening suite. See [`docs/STATUS.md`](docs/STATUS.md).

---

## What AtlasCore is

A lean, single-box pipeline that turns approved public sources into searchable, cited, provenance-rich evidence for your own use:

```text
Source Registry -> Scheduler -> Fetcher (RSS/API) -> Validation Gate
  -> Immutable Raw Store (disk) -> Parse -> Normalize -> Deduplicate
  -> Enrich -> Index (Postgres FTS + pgvector + time-series) -> Alert -> MCP/CLI
```

Components:

- **Source registry** with default-deny policy (Git-tracked YAML mirrored into Postgres).
- **Connector framework** for RSS and official/free APIs (browser only as a rare, approved exception).
- **Postgres-backed job queue** (`SELECT ... FOR UPDATE SKIP LOCKED`) with a DLQ status — no external broker.
- **Immutable raw store** — gzipped, content-addressed payloads on local disk.
- **One PostgreSQL 16 instance** doing relational + full-text (`tsvector`) + vector (`pgvector`) + partitioned time-series.
- **Enrichment** — language, NER, topics, embeddings, and cached, cited summaries. MVP ships deterministic, dependency-free default backends (rule-based NER, hashing embeddings, extractive summaries) so it runs offline; spaCy / sentence-transformers / hosted-LLM backends are pluggable behind optional extras with no schema change.
- **Rule-based alerts** with dedupe and evidence document IDs, delivered over a webhook / ntfy / SMTP / log channel (which also backs the daily digest).
- **Read-only MCP server** over stdio — the agent surface your agents use (6 read tools shipped; `get_raw_document` deferred).
- **A Typer CLI (`atlas`)** — the only ingestion-management surface; agents never trigger crawls or change source policy.

**Architecture rule:** MCP and agents depend on the *processed* stores; the ingestion/control plane must **not** depend on MCP. Health and operations are CLI-driven (`atlas health` / `atlas digest`), not gated through MCP.

---

## The three decisions that shape everything

1. **Internal-only** — never redistribute data. Personal-research posture; always keep attribution.
2. **FOSS boundary is code, not data** — publish connectors/schemas/code, never harvested data or private credentials.
3. **RSS + official/free APIs only** — no browser fleet, no social-platform ingestion.

These keep the system lean, compliant, and cheap to run unattended.

---

## Quickstart

> Prerequisites: Python 3.12+, [`uv`](https://github.com/astral-sh/uv), Docker + Docker Compose. PostgreSQL 16 with `pgvector` and `pg_trgm` runs in Compose.

```bash
# 1. Clone (private repo for now)
git clone <your-remote>/atlascore.git
cd atlascore

# 2. Install deps + init local env (copies .env.example -> .env, sets up uv)
make setup

# 3. Run the local stack (Postgres 16 + pgvector [+ optional Meilisearch])
make dev

# 4. Apply Alembic migrations
make migrate

# 5. Seed fixtures / demo sources
make seed
```

Common operator commands (the `atlas` CLI is the only ingestion-management surface):

```bash
atlas source validate path/to/source.yaml   # validate a source YAML against the canonical schema
atlas source test <source_id>                # dry-run a source (policy + connector check)
atlas crawl once <source_id>                 # one manual crawl
atlas parse <raw_id>                         # re-parse a raw document (raw stays immutable)
atlas enrich <doc_id>                        # run enrichment on a document
atlas health                                 # source_health + DLQ depth + liveness check
atlas digest                                 # emit the daily digest via the alert channel
atlas alerts run                             # evaluate alert rules
atlas mcp dev                                # run the MCP server over stdio
atlas backup                                 # nightly pg_dump + raw-dir sync
atlas purge <doc_id|entity_id>               # audited erasure (incl. raw + full cascade)
```

Developer quality gates:

```bash
make format      # ruff format
make lint        # ruff lint
make typecheck   # mypy / pyright
make test        # unit + contract + integration (no live network in unit)
make security    # SSRF / sanitizer / secrets-in-columns / prompt-injection regressions
make smoke       # local end-to-end smoke (source -> ingest -> ... -> alert -> backup -> purge)
```

> The import/packaging layout is a single `src/atlascore` import root (ADR 0012). Integration and
> smoke tests need a reachable Postgres 16 (`make dev`); they skip cleanly when none is present, so
> `make test` stays green offline (unit + contract only).

---

## Repository layout (summary)

The import/packaging layout is a single `src/atlascore` root (ADR 0012). The spec's `packages`
(libraries) vs `apps` (runnable roles) split is preserved as the subpackage names — marked `[lib]`/
`[app]` below — so a doc reference like `packages/security/secrets` means `atlascore.security.secrets`.

```text
atlascore/
  README.md  CLAUDE.md  CHANGELOG.md  CONTRIBUTING.md  SECURITY.md  LICENSE
  .gitignore  .env.example  .pre-commit-config.yaml  pyproject.toml  uv.lock
  alembic.ini  .secrets.baseline  Makefile
  .github/        workflows/ci.yml, dependabot.yml
  src/atlascore/  # the single import root = the atlascore distribution
    core/         # [lib] config, logging, IDs (UUIDv7/ULID), errors, clock
    schemas/      # [lib] Pydantic v2 models — canonical shape for migrations
    connectors_sdk/   # [lib] connector interface + test harness
    connectors/   # [lib] rss/ fred/ sec/ nvd/ github/ arxiv/
    parsers/      # [lib] article/ filing/ macro/
    enrichment/   # [lib] language/ ner/ topics/ embeddings/ summaries/
    storage/      # [lib] postgres/ rawstore/ search/
    security/     # [lib] url_safety/ sanitization/ secrets/ policies/
    alerts/       # [lib] rules/ delivery/
    cli/          # [app] Typer CLI; the only ingestion-management surface; entry point `atlas`
    mcp_server/   # [app] FastMCP over stdio; read tools
    worker/       # [app] fetch/parse/enrich loop
    scheduler/    # [app] APScheduler entrypoint; EXACTLY ONE instance
    api/          # [app] OPTIONAL thin read-only FastAPI (only if a dashboard is wanted)
  infra/          # docker-compose.yml, backup.sh
  migrations/     # alembic (postgres only): env.py + versions/
  tests/          # unit/ integration/ fixtures/
  docs/           # PROJECT-PLAN, STATUS, architecture, connectors, schemas,
                  # security, compliance, mcp, runbooks, decisions/ (ADRs)
```

Rule: do not add top-level directories without a clear reason.

---

## Documentation

- **[`CLAUDE.md`](CLAUDE.md)** — the agent operating contract and the **first file to read** at the start of any session. Identity, mission/boundaries, hard prohibitions, session rituals, architecture, the 12 canonical tables, the 7 MCP tools, security, compliance, multi-worker coordination, and the definition of done.
- **[`docs/PROJECT-PLAN.md`](docs/PROJECT-PLAN.md)** — point-in-time rationale, architecture narrative, source categories, MVP backlog (B1–B15), roadmap, risks, cost.
- **[`docs/STATUS.md`](docs/STATUS.md)** — the living board: current block, Done / In progress / Next / Known issues / Open questions. Updated every session.
- **[`docs/architecture/README.md`](docs/architecture/README.md)** — components, data flow, concurrency/multi-worker model.
- **[`docs/connectors/README.md`](docs/connectors/README.md)** — connector lifecycle and canonical source YAML.
- **[`docs/schemas/README.md`](docs/schemas/README.md)** — the 12 canonical tables and design resolutions.
- **[`docs/security/README.md`](docs/security/README.md)** — SSRF/URL safety, sanitization, secrets, prompt-injection.
- **[`docs/compliance/README.md`](docs/compliance/README.md)** — internal-only posture, honest PII framing, retention classes.
- **[`docs/mcp/README.md`](docs/mcp/README.md)** — the 7 read-oriented MCP tools.
- **[`docs/runbooks/README.md`](docs/runbooks/README.md)** — backups/restore, purge, backfill/reindex, secrets at deploy.
- **[`docs/decisions/`](docs/decisions/)** — ADRs (`0000-template.md`, `0001`–`0025`, plus `README.md` index).

---

## Current status

**MVP complete — backlog blocks B1–B15 are done.** See [`docs/STATUS.md`](docs/STATUS.md) for the live board and [`docs/PROJECT-PLAN.md`](docs/PROJECT-PLAN.md) §MVP for the backlog.

The full pipeline works end to end and unattended: an approved source is fetched (SSRF-guarded, conditional), the raw payload is captured immutably, parsed into a canonical `document`, enriched (language → NER → topics → embeddings), indexed for full-text search, summarized (cached + cited), and watched by rule-based alerts — all behind a read-only MCP surface and a `atlas` control-plane CLI, with nightly backups and an audited `atlas purge` erasure path. `make smoke` runs the whole chain; `make security` runs the hardening regressions.

The MCP surface ships **6 read tools**: `search_documents`, `get_macro_series`, `get_entity_mentions`, `get_document_summary`, `get_source_health`, `get_alerts`. The 7th, `get_raw_document` (the injection-sensitive raw-HTML tool), is intentionally deferred. **Deliberately deferred past the MVP** (see `docs/STATUS.md` Known issues): the timed scheduler/worker loop, real embedding/LLM backends, SEC full-filing (Tika) + NVD keyed tier, an alert watchlist + lifecycle, and `time_series_point` yearly partitions. Scope is frozen until the current set runs unattended for a month.

---

## License

**Private repository, all rights reserved (proprietary placeholder).** The `LICENSE` file is a proprietary placeholder while the repo is private. The FOSS boundary is **code, not data**: the intent is to publish the code (choosing Apache-2.0 or MIT at publish time) while never publishing harvested data or private credentials. No hardcoded private credentials or licensed feeds are ever committed.
