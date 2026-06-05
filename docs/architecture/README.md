# AtlasCore — Architecture Deep-Dive

> **Date:** 2026-06-03
> **Status:** Authoritative architecture reference. Consistent with `ATLASCORE CANONICAL DECISIONS SPEC v1`.
> **Scope:** The processing topology, every layer, the concurrency/multi-worker model, graceful degradation, the ingestion-never-depends-on-MCP rule, and operational health.

AtlasCore is **a governed, single-operator data fabric for your own agents and dashboards.** It collects data from RSS feeds and free/official APIs, preserves raw evidence, normalizes and lightly enriches it, makes it searchable, exposes read-only tools to agents over MCP, and fires a few rule-based alerts. It runs unattended on **one box**.

Foundational constraints that shape every layer:

- **One operator.** No multi-tenancy, no RBAC, no identity provider.
- **Internal-only.** Never redistribute data; FOSS boundary is **code, not data**.
- **RSS + official/free APIs only.** No browser fleet, no social-platform ingestion.
- **Single box.** One PostgreSQL 16 instance, one local-disk raw store, no external broker/search-cluster/vector-DB/OLAP/K8s.
- **Provenance-first.** Every result traces to source, raw payload, job, parser version, and schema version.

---

## 1. Canonical Flow

The full pipeline, end to end:

```text
Source Registry  ->  Scheduler  ->  Fetcher (RSS/API)  ->  Validation Gate
  ->  Immutable Raw Store (disk)  ->  Parse  ->  Normalize  ->  Deduplicate
  ->  Enrich  ->  Index (Postgres FTS + pgvector + time-series)  ->  Alert  ->  MCP / CLI
```

Each arrow is mediated by the **`crawl_job` queue**: the scheduler enqueues `fetch` jobs; a fetch produces a `raw_document` plus a `parse` job; a parse produces a `document` plus an `enrich` job; enrichment can enqueue `alert` evaluation; a periodic `freshness` job recomputes `source_health`. Jobs are durable, idempotent, and replay-safe (Section 6).

**The control plane (registry, scheduler, workers, queue, raw store, storage) never depends on MCP.** MCP and any agents are strictly downstream readers of processed stores. Agents never trigger crawls and never change source policy (Section 7).

---

## 2. ASCII Data-Flow Diagram

```text
                          ┌──────────────────────────────────────────────┐
                          │              CONTROL PLANE (MCP-free)          │
                          │                                                │
   Git-tracked YAML       │   ┌───────────────┐      ┌──────────────────┐ │
   source/*.yaml  ───────────▶│ Source        │      │ Scheduler        │ │
   (source of truth)      │   │ Registry      │─────▶│ EXACTLY ONE inst.│ │
                          │   │ (source tbl,  │ due  │ APScheduler/cron │ │
                          │   │  default-deny)│      │ + pg advisory    │ │
                          │   └───────────────┘      │   lock guard     │ │
                          │           ▲              └────────┬─────────┘ │
                          │           │ mirror                │ enqueue   │
                          │           │                       ▼           │
                          │   ┌───────────────────────────────────────┐   │
                          │   │  crawl_job  (durable queue + DLQ)      │   │
                          │   │  status: pending→claimed→running→done  │   │
                          │   │          ↘ failed → dead (DLQ)         │   │
                          │   │  claim: FOR UPDATE SKIP LOCKED         │   │
                          │   └───────────────────────────────────────┘   │
                          │           │ claim (SKIP LOCKED)                │
                          │           ▼                                    │
                          │   ┌──────────────────┐                        │
                          │   │ Fetch Worker      │  per-host token bucket │
                          │   │ (EXACTLY ONE in   │  + SSRF/URL-safety     │
                          │   │  MVP; advisory-   │  + ETag/Last-Modified  │
                          │   │  lock guarded)    │                        │
                          │   └────────┬─────────┘                         │
                          │            │ bytes + headers                   │
                          │            ▼                                    │
                          │   ┌──────────────────┐                        │
                          │   │ Validation Gate   │  MIME/size/status/SSRF │
                          │   │ (reject → fail/   │  redirect re-validate  │
                          │   │  DLQ, no parse)   │                        │
                          │   └────────┬─────────┘                         │
                          │            │ accepted                          │
                          │            ▼                                    │
                          │   ┌──────────────────┐   gzip, content-        │
   LOCAL DISK ◀───────────────│ Immutable Raw     │   addressed path        │
   raw/<hash>.gz          │   │ Store (disk)      │   raw_document row      │
                          │   └────────┬─────────┘                         │
                          │            │ enqueue parse                     │
                          │            ▼                                    │
                          │   ┌──────────────────┐                        │
                          │   │ Parse / Normalize │  trafilatura+fallback, │
                          │   │ Workers (scale-   │  macro/filing parsers  │
                          │   │  out OK)          │                        │
                          │   └────────┬─────────┘                         │
                          │            │ canonical record                  │
                          │            ▼                                    │
                          │   ┌──────────────────┐                        │
                          │   │ Deduplicate /     │  URL + content_hash,   │
                          │   │ Entity-Link       │  cluster_id, mention   │
                          │   └────────┬─────────┘                         │
                          │            │ enqueue enrich                    │
                          │            ▼                                    │
                          │   ┌──────────────────┐                        │
                          │   │ Enrichment        │  language→NER→topics→  │
                          │   │ Workers (scale-   │  embeddings, summaries │
                          │   │  out OK)          │  (pluggable backends)  │
                          │   └────────┬─────────┘                         │
                          │            ▼                                    │
   ┌──────────────────────────────────────────────────────────────────┐  │
   │  POSTGRES 16  (relational + FTS tsvector + pgvector + time-series) │  │
   │  source · crawl_job · raw_document(ptr) · document · summary ·     │  │
   │  embedding · time_series_point · entity · mention · alert ·        │  │
   │  audit_log · source_health                                         │  │
   └──────────────────────────────────────────────────────────────────┘  │
                          │            │            │                      │
                          │   freshness job   alert rules                  │
                          │            │            │                      │
                          │            ▼            ▼                      │
                          │   ┌──────────────┐  ┌──────────────┐          │
                          │   │ source_health │  │ alert + deliv │          │
                          │   │ (snapshot)    │  │ (webhook/ntfy │          │
                          │   └──────────────┘  │  /email)      │          │
                          │                     └──────────────┘          │
                          └────────────────────────────────────────────────┘
                                       │ READ-ONLY
                ┌──────────────────────┼───────────────────────┐
                ▼                      ▼                        ▼
        ┌──────────────┐      ┌──────────────┐         ┌──────────────┐
        │ CLI (atlas)  │      │ MCP server    │         │ OPTIONAL API │
        │ control      │      │ (FastMCP,     │         │ thin FastAPI │
        │ plane +      │      │  stdio, 7     │         │ /v1/health   │
        │ atlas health │      │  read tools)  │         │ (dashboard)  │
        └──────────────┘      └──────────────┘         └──────────────┘
```

Solid downward arrows are the **ingestion/control plane**. The bottom three boxes are **read-only consumers** of the processed Postgres stores; none of them feed back into ingestion.

---

## 3. Layers in Detail

### 3.1 Source Registry

- **Source of truth:** git-tracked YAML under `source/*.yaml`, mirrored losslessly into the `source` table (12-table model, Section 4 of the canonical spec).
- **Default-deny policy.** `policy_status` defaults to `unknown`. Only `approved` (or `restricted` with encoded constraints) is fetched. Missing / `unknown` / `pending` / `prohibited` => disabled. `atlas source validate` enforces shape; the scheduler refuses to enqueue a disabled source.
- **Lossless round-trip.** The `source` table carries everything the YAML carries: `rate_limit` jsonb, `retention` jsonb (`{raw, normalized, audit?}`), `freshness` jsonb (`{target, warn_after, error_after}`), `credential_ref` (env-key name, **never** a secret value), plus a **`config` jsonb** column that homes the fields the YAML carries that have no first-class column: `policy.allowed_uses`, `policy.robots`, `fetch.connector`, `fetch.schedule` (cron), and `fetch.timeout_seconds`. `parser_ref` names a parser, not a fetch connector — the connector dotted-path lives in `config.fetch.connector`. **The scheduler reads each source's cron schedule from `config.fetch.schedule`.**
- **Robots posture.** `policy.robots` is a **policy input** consulted by the human operator when setting source policy. It is **not** a general runtime gate for API/RSS sources. A runtime robots check is built only inside an approved HTML/browser connector — and no HTML/browser source ships in the MVP (B1–B15), so no runtime robots gate exists yet.

### 3.2 Scheduler

- **EXACTLY ONE instance.** Two schedulers would double-fire every source. The invariant is enforced three ways, defense-in-depth: a single systemd unit / single compose service, **and** a PostgreSQL advisory lock acquired at startup (`pg_advisory_lock` on a fixed scheduler key) — a second scheduler that cannot acquire the lock refuses to start.
- **Responsibility:** compute *due* sources (next-fire from `config.fetch.schedule` cron, gated on `enabled` + `policy_status`), and enqueue exactly one `fetch` `crawl_job` per due source with a deterministic `dedupe_key`. It also enqueues periodic `freshness` jobs.
- The scheduler **never fetches** and **never parses**. It only enqueues. This keeps it cheap, restartable, and trivially singleton.
- **As-built (B19, [ADR 0030](../decisions/0030-b19-scheduler-and-worker-loop.md)):** `atlas scheduler run` holds the advisory lock (`scheduler.lock`) and enqueues one **tick-bucketed** `fetch` job per due source (idempotent across overlapping ticks). System-wide `freshness`/`alert`/`partition`/`audit` work runs **inline** in the scheduler on per-task cadences rather than as queued jobs — `crawl_job.source_id` is `NOT NULL`, so these source-less tasks can't be queued without a migration, and the single-instance scheduler is the natural home for them. The per-item pipeline is drained by `atlas worker run` (`worker.loop`: fetch→parse→enrich, fetch under the single-fetch lock). `atlas health`/`atlas digest` report real scheduler liveness via `scheduler_active`.

### 3.3 Job Queue (`crawl_job`)

- One durable Postgres table is the entire queue — **no external broker** (no Redis/Celery/RabbitMQ/Kafka).
- **Status machine:** `pending → claimed → running → done`, with `failed` and terminal `dead` (the DLQ). Jobs exceeding `max_attempts` move to `status=dead`, inspectable via CLI.
- **Job types:** `fetch | parse | enrich | alert | freshness`.
- **Claim semantics:** `SELECT ... FOR UPDATE SKIP LOCKED` (Section 6).
- **Idempotency:** unique `dedupe_key` per job; replays never duplicate work.
- **Retention:** 90 days; `done`/`dead` pruned past the window.

### 3.4 Fetcher (RSS / API)

- HTTP via `httpx`; RSS via `feedparser`. Sends conditional requests (`If-None-Match` from stored `etag`, `If-Modified-Since` from `last_modified`); a `304` is a cheap no-op success that still refreshes `source_health`.
- Uses the **transparent `DEFAULT_USER_AGENT`** (project UA + contact) where required — e.g. SEC EDGAR.
- **Per-host rate limiting** (Section 6.3) and **SSRF/URL-safety** (Section 5) are enforced *before* every connect, including on every redirect hop.
- On transient failure: exponential backoff + jitter, respecting `Retry-After` (Section 6.6).
- **Before persisting** `crawl_job.last_error` (and any other free-form sink), the fetcher passes the value through the single `sanitize_for_storage` helper in `packages/security/secrets` so that `api_key=...` query params, userinfo, and `Authorization`/`Cookie` headers never land in Postgres or backups (Section 5.2).

### 3.5 Validation Gate

A hard gate between bytes-on-the-wire and the raw store. It checks **MIME type, size, HTTP status, and SSRF/URL-safety** (the final resolved URL after redirects). A rejected response **fails the job** (and ultimately lands in the DLQ if it keeps failing) and **never reaches the parser**. Nothing untrusted is parsed before it has been captured and gated.

### 3.6 Immutable Raw Store

- **Local disk, gzipped, content-addressed.** Path derives from `content_hash`; identical bytes collapse to one file.
- **Immutable.** A `raw_document` row is written once and never mutated. The single documented exception is the audited `atlas purge` path (R3), which deletes the file + row and writes a **retained** `audit_log` entry.
- Encrypt at rest only if the host is shared/untrusted; not required for a single-operator box.
- Storing raw bytes here (not in Postgres) keeps the database lean and backups fast.

### 3.7 Parse / Normalize

- **Article parser:** `trafilatura` with readability/newspaper-style fallback -> canonical `document`. **Macro/filing parsers** produce `time_series_point` rows and filing records respectively.
- Parsing **always reads from the immutable raw**, so it is fully reproducible: re-running a pinned `parser_version` against the same `raw_document` reproduces output.
- **Versioning (R2):** `document` holds **current state only**, last-write-wins. Re-parsing **updates the row in place**; old parsed versions are not retained but *are* reproducible from the immutable raw. `content_hash(parsed)` detects when a re-parse changed output. No versioned parsed-document table.
- Outputs carry `parser_version`, `schema_version`, attribution (source, URL, author, timestamp), and the FK to the `raw_document` they were parsed from.

### 3.8 Deduplicate / Entity-Link

- **Dedupe:** `document.canonical_url` unique (where not null) + parsed `content_hash`; connectors additionally enforce `unique(source_id, external_id/guid)` so replayed feed items never duplicate. Near-duplicate / syndicated copies share a `cluster_id` so we don't embed or summarize boilerplate twice.
- **Entity-link:** NER output resolves to `entity` rows (with `aliases`, `identifiers`, `merged_into` merge history) and writes `mention` rows linking entity ↔ document with confidence and a context snippet. `document.entity_ids[]` and `document.topics[]` are **intentionally non-referential** array columns — they are reconciled only by the purge job, never by FKs (R17).

### 3.9 Enrichment

- Pipeline order: **language -> NER -> topics -> embeddings -> summaries.**
- **Pluggable backends.** `EmbeddingBackend` (`local` sentence-transformers | `openai` | `google` | test stub) and `SummaryBackend` (`LLM_BACKEND=local|api|none`). Config: `EMBEDDING_BACKEND`, `EMBEDDING_MODEL`, `EMBEDDING_DIM`, `LLM_BACKEND`, `LLM_MODEL`. Cloud is a first-class supported option (the operator may lack a powerful local machine); local remains the option for offline/privacy.
- **One active embedding model + dim per deployment (R8).** `pgvector vector(N)` needs a fixed dimension per ANN index; switching backend/dim is a documented, bounded, resumable **backfill + reindex** runbook, never an online change. `dim` + `model_version` are stored for provenance and mismatch detection.
- **Idempotent enrichment.** `embedding` carries `unique(document_id, chunk_index, model_name, model_version)`; re-embedding an unchanged `source_hash` is a **no-op (skip)** (R13). `summary` is content-hash cached on `unique(source_hash, model_name, model_version, prompt_version)`; an identical (content, model, prompt) tuple is **never re-summarized** (R4). **Dedupe-before-embed/summarize** to avoid paying for boilerplate.
- **Cost guard.** When `LLM_BACKEND=api`, a **hard `LLM_MONTHLY_SPEND_CAP_EUR`** applies.
- **PII / data-egress reality (compliance):** when `EMBEDDING_BACKEND` or `LLM_BACKEND` is a cloud backend, document text — which (per the honest PII posture) inherently contains personal data such as names and quotes — **egresses to a third-party (and likely third-country) processor.** The recommended posture is: prefer **local** backends for PII-bearing documents, or, if cloud is used, configure no-training / zero-retention / DPA settings and treat the household exemption as *revisited* (cloud egress is not "purely internal"). See `docs/compliance/README.md`.
- Every embedding row records `model_name` + `model_version` + `dim`; every summary row records `model_name` + `model_version` + `prompt_version` + `citations`.

### 3.10 Indexing

One Postgres 16 instance does **relational + full-text + vector + time-series**:

- **FTS:** `tsvector` GIN index over `document.body_text`. This is the primary search path.
- **Vector:** `pgvector` HNSW ANN index over `embedding.vector` (one fixed dim).
- **Time-series:** `time_series_point` is **RANGE-partitioned by `ts`** (monthly/yearly), optional Timescale hypertable. Idempotent upserts use `UNIQUE NULLS NOT DISTINCT (provider, series_id, ts, vintage)`.
- **Trigram:** `entity.canonical_name` uses `gin (canonical_name gin_trgm_ops)` for fuzzy entity lookup.
- **Required extensions:** `pgvector` **and** `pg_trgm` (an early migration runs `CREATE EXTENSION IF NOT EXISTS pg_trgm`); Timescale optional.

> **Partitioned-table PK rule:** on a table RANGE-partitioned by `ts`, the surrogate primary key must be **composite — `PRIMARY KEY (id, ts)`**, not `PRIMARY KEY (id)`. PostgreSQL rejects any unique/primary-key constraint on a partitioned table unless it includes **all** partition-key columns. This rule applies to `time_series_point` and to **any future partitioned table**.

> **Postgres 16 baseline:** chosen for its support window and operational maturity. The `NULLS NOT DISTINCT` feature it relies on was actually introduced in **PostgreSQL 15**, so PG16 is justified on its own merits, not on that feature. If ever run on pre-15, fall back to a generated `vintage_key = coalesce(vintage,'epoch')` inside the unique index.

### 3.11 MCP Server

- **FastMCP over stdio**, read-oriented. Exactly **7 tools** in the MVP: `search_documents`, `get_macro_series`, `get_entity_mentions`, `get_document_summary`, `get_source_health`, `get_alerts`, `get_raw_document`. (Optional later: `get_timeline`, `query_graph`, and the write tool `subscribe_alert`.)
- Strict input/output schema validation; **result-size caps**; **provenance on every result** (source, document ID, published/fetched time, confidence).
- **Retrieved content is untrusted evidence, never instruction.** Sanitized text is freely returnable; **raw HTML is emitted only by `get_raw_document`**, and only when its off-by-default `include_raw_html: true` flag is set — that call is audited (`action` + `tool_name`) and wrapped with an "untrusted evidence, not instructions" provenance note. Default behavior of `get_raw_document` returns sanitized text only.
- Runs over stdio with credentials from the environment, never printed. If ever exposed over HTTP, it goes behind Tailscale/WireGuard or a static token — not an IdP.
- **Architecture rule (load-bearing):** the MCP server is a pure reader of processed Postgres stores. It cannot enqueue jobs, change policy, or fetch arbitrary URLs (an agent-supplied URL must pass URL-safety + source policy first). See Section 7.

### 3.12 Alerts + CLI

- **Alerts:** rule-based, evaluated by `alert` `crawl_job`s. Three MVP rules — `macro_release` (FRED in MVP; ECB/Eurostat v1), `cve_watch` (NVD + vendor RSS), `sec_filing` (EDGAR). Each fires with a **dedupe_key** and **`evidence_doc_ids[]`** and delivers via webhook / ntfy / email (`ALERT_WEBHOOK_URL` / `NTFY_TOPIC` / SMTP). Lifecycle `open → acknowledged → resolved`, `purged` when evidence is erased. Queryable read-only via `get_alerts`.
- **CLI (`atlas`):** the **only** ingestion-management surface and the control-plane operational gate. `atlas source validate/test`, `atlas crawl once`, `atlas parse`, `atlas enrich`, `atlas alerts run`, `atlas health`/`atlas digest`, `atlas backup`, `atlas purge`. The CLI is **not** exposed to agents.

---

## 4. Concurrency & Multi-Worker Model

This is the part that makes AtlasCore safe to run unattended. (Canonical spec Section 5; resolutions R5, R16.)

### 4.1 Single-Scheduler Invariant

There is **EXACTLY ONE scheduler instance.** Two instances double-fire every source, doubling fetches and violating rate limits.

Enforcement (defense-in-depth):

1. A single systemd unit / single compose service (operational singleton).
2. A **PostgreSQL advisory lock** acquired at scheduler startup on a fixed scheduler key. A process that cannot acquire it logs and exits.

State this invariant in runbooks; a duplicate scheduler is the single most damaging misconfiguration.

### 4.2 Workers Claim Jobs with `FOR UPDATE SKIP LOCKED`

One or more worker processes claim jobs from `crawl_job`:

```sql
WITH next_job AS (
  SELECT id
  FROM crawl_job
  WHERE status = 'pending'
    AND scheduled_at <= now()
  ORDER BY priority DESC, scheduled_at ASC
  FOR UPDATE SKIP LOCKED
  LIMIT 1
)
UPDATE crawl_job j
SET status = 'claimed',
    claimed_at = now(),
    claimed_by = :worker_id,
    attempt = attempt + 1
FROM next_job
WHERE j.id = next_job.id
RETURNING j.*;
```

`SKIP LOCKED` lets N workers pull disjoint jobs concurrently with no central coordinator and no double-claim. The claim, run, and terminal transition (`done`/`failed`) are each transactional.

### 4.3 Per-Host Rate Limiting — MVP vs Scale-Up (R5)

Rate limiting is **host-scoped** and must stay correct under concurrency, because per-host limits (e.g. **SEC EDGAR's 10 req/s**) are a hard compliance line.

- **MVP — EXACTLY ONE fetch worker.** Fetch concurrency per host = 1, enforced by **in-process per-host token buckets**. Parse and enrich workers may scale out freely (they touch no remote host). A second fetch worker would silently break the per-host guarantee, so a **startup guard mirrors the scheduler's**: the fetch role acquires a **pg advisory lock** (or asserts `FETCH_WORKERS=1`) and **refuses to start a second fetch worker** until the documented scale-up path is enabled (R16).
- **Scale-up (documented, when > 1 fetch worker is genuinely needed).** Move the token bucket into Postgres: a **`host_rate_state`** table holding a per-host token bucket, **or** `pg_advisory_xact_lock(hashtext(host))` + decrement-on-claim. Either makes the per-host limit correct across processes. Only then is the single-fetch-worker startup guard lifted.

`Retry-After` is **always** respected, in both modes.

### 4.4 Idempotency Keys

Jobs must be safe to replay:

- **Per-job:** deterministic `crawl_job.dedupe_key` (unique). Re-enqueuing the same logical job is a no-op.
- **Per-document:** content-hash upserts; `unique(source_id, external_id/guid)` so replayed feed items never duplicate.
- **Per-embedding:** `unique(document_id, chunk_index, model_name, model_version)`; unchanged `source_hash` => skip (R13).
- **Per-summary:** `unique(source_hash, model_name, model_version, prompt_version)`; identical tuple => cache hit, no recompute (R4).
- **Per-time-series-point:** `UNIQUE NULLS NOT DISTINCT (provider, series_id, ts, vintage)` => idempotent upsert even when `vintage` is null (R1).

### 4.5 Visibility-Timeout Reclaim (Crash Safety)

A worker can crash mid-job, leaving a row stuck in `claimed`/`running`. A reaper (run by a worker or the scheduler) **reclaims stale claims**: any job whose `claimed_at` is older than the **visibility timeout** is reset to `status=pending` (its `attempt` count is preserved, so it still walks toward `max_attempts`/`dead`). This guarantees no job is lost to a crash and none is stuck forever.

### 4.6 Backoff and Circuit Breaker

- **Backoff:** exponential + jitter on transient failures (DNS / connect / read / `408` / `429` / `5xx`). **Respect `Retry-After`.** Do **not** retry most `4xx` (they won't get better by retrying).
- **Dead-letter:** a job that exhausts `max_attempts` moves to `status=dead` (DLQ), inspectable from the CLI.
- **Circuit breaker:** after **N consecutive failures**, a source is circuit-broken — `source_health.status=cooling_down` — and it **surfaces in the daily digest**. This stops AtlasCore from hammering a dead endpoint and tells the operator about it.

---

## 5. Security Surfaces in the Architecture

### 5.1 SSRF / URL-Safety (authoritative; applies to the fetcher AND any agent-supplied URL)

Every outbound fetch — by the fetcher *or* via any agent-supplied URL reaching the control plane — passes URL-safety **before connect**:

- **Scheme allowlist:** `http`/`https` only. Reject everything else (`file:`, `gopher:`, `ftp:`, `data:`, …).
- **Reject** loopback (`127.0.0.0/8`, `::1`), RFC1918 (`10/8`, `172.16/12`, `192.168/16`), **CGNAT `100.64.0.0/10`**, link-local `169.254.0.0/16` and `fe80::/10`, ULA `fc00::/7`, IPv4-mapped IPv6, and cloud metadata endpoints `169.254.169.254` / `fd00:ec2::254`.
- **Resolve the hostname and validate ALL resolved IPs before connect, then pin to the validated IP** (anti-DNS-rebinding).
- **Re-validate every redirect hop** against the same rules and **cap the redirect count.**

This is implemented in `packages/security/url_safety` and is the gate at both the Fetcher (3.4) and Validation Gate (3.5) — and is the reason `get_raw_document`/MCP cannot be coerced into fetching internal endpoints.

### 5.2 Secrets Never Reach Free-Form Sinks

`crawl_job.last_error`, `crawl_job.payload`, and `audit_log.detail` are free-form `jsonb`/`text` sinks that could otherwise capture an `api_key=...` URL (FRED uses `?api_key=`) or an `Authorization`/`Cookie` header straight into Postgres — and from there into `pg_dump` backups, which the repo-only gitleaks/detect-secrets scan never sees. Therefore:

- A single **`sanitize_for_storage`** helper in `packages/security/secrets` redacts known-secret patterns and strips URL query/userinfo plus `Authorization`/`Cookie` headers **before** any value is persisted to those columns.
- **Forbidden:** storing resolved credential values or credential-bearing URLs in any `jsonb`/text column. Backups inherit this guarantee.
- The B15 secrets-in-logs regression test covers `last_error` and `audit_log.detail`.

### 5.3 FK Behavior and the Purge Path (R3, R17)

- **FKs use `ON DELETE NO ACTION`/`RESTRICT`** so the application-driven purge ordering is DB-enforced and an accidental `ON DELETE CASCADE` can't over-delete on entity purge.
- **`atlas purge <doc_id>` cascade:** delete `embedding`, `mention`, `summary` rows referencing the doc; **scan `summary.member_doc_ids[]`** (GIN-indexed) and delete/recompute any **multi-doc** summary that links the doc only via that array (so purged personal data can't survive in `summary_text`/`citations`); remove from FTS; delete the `document` row; delete the `raw_document` file + row; scan `alert.evidence_doc_ids[]` and remove the id (an alert that loses **all** evidence => `status=purged`); write a **retained** `audit_log` entry recording action + hashes (never the personal data). `atlas purge <entity_id>`: delete the `entity` + its `mentions`; documents survive. Purge is **idempotent**.
- **Array-of-id columns** (`document.entity_ids`, `document.topics`, `alert.evidence_doc_ids`, `summary.member_doc_ids`) are intentionally **non-referential** and reconciled **only by the purge job**.
- `audit_log` carries a **`retention_class`** column: erasure entries are `purge` (retained forever, so erasure stays provable); routine entries are `routine` and the rotation job deletes only `routine` past the window.

---

## 6. Graceful Degradation

AtlasCore degrades layer-by-layer instead of failing whole:

| If this is down / absent | What still works |
| --- | --- |
| Enrichment (NER / topics / embeddings) | Fetch, parse, **FTS search**, alerts, MCP `search_documents` — enrichment is additive. |
| Vector search (`pgvector` / ANN) | **FTS** (`tsvector`) search still answers `search_documents`. |
| LLM backend (`LLM_BACKEND=none` or API down) | `get_document_summary` returns **cached** summaries; otherwise `null` **with a reason**, never a fabricated summary. |
| Cloud embedding backend unreachable | Already-embedded docs remain searchable by vector; new docs queue for re-embed; FTS unaffected. |
| A single source circuit-broken (`cooling_down`) | All other sources keep ingesting; the broken one surfaces in the digest. |
| Meilisearch (optional) absent | Postgres FTS is the default and sufficient path; Meilisearch is only used if FTS is outgrown. |
| Scheduler down | In-flight jobs finish; no new fetches enqueue until it restarts. No data corruption — just staleness, which the freshness job + digest surface. |

Principle: **search works without enrichment; FTS works if vector search is down; summaries fail closed (null + reason), never hallucinate.**

---

## 7. The Ingestion-Never-Depends-on-MCP Rule

This is an architectural boundary, not a guideline:

- **Direction of dependency is one-way.** Source registry, scheduler, queue, workers, raw store, parse/normalize/enrich, indexing, alerts — none of them import or call the MCP server.
- **Agents are pure readers.** They query processed Postgres stores through the 7 read tools. They **cannot** enqueue a `crawl_job`, change `policy_status`, edit a source, mutate raw, or trigger a crawl.
- **The control plane is CLI-driven.** Anything that changes ingestion happens through `atlas …`. If MCP were offline forever, ingestion would run unchanged.
- **Why it matters:** it keeps prompt-injected/malicious retrieved content from ever steering ingestion or policy. The worst an injected document can do is appear as evidence in a tool result — it can never become an instruction to the control plane.

---

## 8. Operational Health

Health is **CLI-driven (control-plane, MCP-independent)** by design — the control plane must not depend on MCP, so MCP `get_source_health` is a **read-only convenience for agents/dashboards, not the operational gate.**

### 8.1 `source_health` and the Freshness Job

- The **`freshness` job** (`job_type=freshness`) runs on a schedule, computing per-source: `freshness_lag = now - coalesce(last_item_published_at, last_success_at)`, `success_rate_24h`, `parse_quality`, `duplicate_rate`, `item_count_recent`, `consecutive_failures`, and `status` (`ok|warn|stale|error|cooling_down`).
- It compares `freshness_lag` against the source's structured `freshness` thresholds: past `warn_after` => `warn` (appears in the digest); past `error_after` => `stale`/`error` (fires a freshness alert).
- `source_health` holds a **current snapshot** (no history table in the MVP).

### 8.2 `atlas health` / `atlas digest`

`atlas health` (alias `atlas digest`) is the operational health surface. It:

1. Reads `source_health` (freshness/error/parse-quality per source).
2. Reads **`crawl_job` DLQ depth** (`status=dead` count) and stuck-claim counts.
3. Performs a **scheduler/worker liveness check** (advisory-lock holder present? recent job activity?).
4. Emits the **daily digest** through the alert delivery channel (webhook / ntfy / email).

Because this is pure CLI + Postgres + delivery, it works even if the MCP server is down. MCP `get_source_health` exposes the same `source_health` snapshot read-only, with **no secrets and no internal hostnames** in its output.

### 8.3 Optional FastAPI `/v1/health`

If the optional read-only dashboard (`apps/api`) is built, it exposes a thin **`GET /v1/health`** that returns the same aggregate (worst-case `source_health.status`, DLQ depth, scheduler liveness) as a JSON document with a request ID. This is an **optional convenience** for an external uptime check; it is **not** required, and like MCP it is a read-only consumer that ingestion never depends on.

---

## 9. Build Order Touchpoints (where these layers land)

The architecture is realized incrementally over the canonical **B1–B15** backlog (`docs/PROJECT-PLAN.md` Section 8). Architecturally load-bearing dependencies:

- **B1** finalizes the Python import/packaging layout (the exact module path behind the `atlas` console script) and stands up Postgres 16 + pgvector + the Alembic harness + `source` schema.
- **B3** introduces the raw store + `crawl_job` queue (SKIP LOCKED + DLQ).
- **B4** adds the fetcher with SSRF/URL-safety + per-host rate limiting (single fetch worker + startup guard).
- **B7** ships `search_documents` **and creates the `audit_log` table** + result caps. **`get_source_health` does NOT ship in B7** — it depends on `source_health`, which arrives in B8.
- **B8** adds `source_health` + the freshness job + `get_source_health` + the `atlas health`/`atlas digest` digest. The B7→B8 dependency is explicit: B8's health surfaces require the `audit_log` table B7 creates and the `source_health` table B8 introduces.

> **Frozen scope discipline:** the 7-tool MCP surface and the current source set are frozen; do not add sources or services until the current set has run unattended for a month.

---

## 10. Cross-References

- **CLAUDE.md** — agent operating contract and entry point.
- **docs/PROJECT-PLAN.md** — rationale, roadmap, the single B1–B15 backlog.
- **docs/STATUS.md** — living state (current block, in-progress claims, known issues).
- **docs/schemas/README.md** — the 12-table canonical data model in full.
- **docs/security/README.md** — SSRF, sanitization, secrets, prompt-injection regressions.
- **docs/compliance/README.md** — internal-only posture, honest PII framing, cloud-egress note, retention classes.
- **docs/mcp/README.md** — the 7 read tools, schemas, provenance, `get_raw_document` flag.
- **docs/runbooks/README.md** — single-scheduler/single-fetch guards, backfill+reindex, backup/restore, purge.
- **docs/decisions/** — ADRs (R1–R8 and related decisions), dated 2026-06-03, status Accepted.
