# AtlasCore — Project Plan

> **Status:** Point-in-time plan + roadmap. **Date:** 2026-06-03. **Brand:** AtlasCore.
> **Project / repository / Python distribution name:** `atlascore`. **CLI command:** `atlas`.
> **Tagline:** *A governed, single-operator data fabric for your own agents and dashboards.*
>
> This is the **rationale + roadmap** document. The authoritative agent operating contract is `CLAUDE.md`; the living state board is `docs/STATUS.md`; full schema detail lives in `docs/schemas/README.md`; deep dives live under `docs/{architecture,connectors,security,compliance,mcp,runbooks}/README.md`; decisions are recorded as ADRs in `docs/decisions/`.
>
> **Formerly AtlasWire.** The project was renamed to AtlasCore; the only remaining `AtlasWire` reference is the redirect note in `AtlasWire-Personal-Plan.md`.

---

## 1. Executive summary

AtlasCore is a small, governed ingestion-and-retrieval system for **one operator**. It collects data from RSS feeds and free/official APIs, preserves raw evidence immutably, normalizes and lightly enriches it, makes it searchable, exposes **read-only** tools to agents over MCP (stdio), and fires a few rule-based alerts. It runs unattended on a single box.

Three foundational decisions shape everything:

1. **Internal-only** — never redistribute data.
2. **The FOSS boundary is code, not data** — publish connectors/schemas/code, never harvested data or private credentials.
3. **RSS + official/free APIs only** — no browser fleet, no social-platform ingestion.

It is **not** a multi-tenant SaaS, not a redistribution platform, and not a general crawler. The single biggest project risk is **over-scope**; the operating discipline is to build the smallest safe thing that preserves compliance, provenance, and reliability, and to freeze scope until the current set of sources runs unattended for a month.

The MVP is a single, ordered backlog of fifteen blocks, **B1–B15** (section 12). This replaces the three older conflicting "build first" / "implementation plan" / "first 10 tasks" lists that previously appeared in the AtlasWire plan; those are deleted and reconciled to B1–B15 here.

**Repo posture:** PRIVATE first, with full open-source-grade hygiene and CI from day one so it can be published later as *code, not data*.

---

## 2. Goals and non-goals

### 2.1 Goals

- **Provenance-first.** Every result traces to source, raw payload, crawl job, parser version, and schema version. Evidence beats confidence.
- **Runs unattended.** Idempotent jobs, retries/backoff, circuit breakers, a DLQ, source-health/freshness checks, and a daily digest that tells the operator when something breaks.
- **Single-box, lean stack.** One PostgreSQL 16 instance does relational + full-text + vector + time-series; a local-disk content-addressed raw store; a Postgres-backed job queue. No external broker, search cluster, vector DB, OLAP store, or Kubernetes.
- **Safe agent access.** A read-oriented MCP server over stdio with strict input/output schemas, result-size caps, provenance on every result, and treating retrieved content as untrusted evidence — never as instructions.
- **Honest compliance.** Internal-only, attribution always kept, a working audited erasure path (`atlas purge`), and no overclaiming of controls (see section 8).

### 2.2 Non-goals (do not build speculatively)

Multi-tenancy, RBAC, OIDC/identity provider, data redistribution, social-platform ingestion, broker APIs, a browser-worker fleet, Kafka/ClickHouse/Neo4j/Qdrant/OpenSearch, Kubernetes, a DPIA program, WORM audit exports, or an admin/write MCP surface. Each of these is added only when a concrete present need appears and is documented in an ADR.

### 2.3 Operating priority order (when goals conflict)

1. Legal/safety correctness (never fetch `prohibited`/`unknown`/`pending` sources; don't collect personal data you don't need; respect rate limits and allowed uses).
2. Provenance / auditability.
3. Security / privacy (secrets out of repo, SSRF safety, content sanitization, prompt-injection separation).
4. Reliability (idempotency, retries, circuit breakers, DLQ, source-health).
5. Scope discipline.
6. Data quality.
7. Clarity over cleverness.

---

## 3. Source categories (right-sized)

AtlasCore targets free/official APIs and RSS only. Every source carries policy metadata before ingestion (default-deny: missing/`unknown`/`pending`/`prohibited` ⇒ disabled).

| Category | Example sources | Access method | Notes / rate posture | MVP block |
|---|---|---|---|---|
| News / articles (RSS) | Publisher and vendor RSS feeds | rss | ETag/Last-Modified conditional GETs; per-host token bucket | B4–B5 |
| Macro / economics | FRED (Federal Reserve), later ECB / Eurostat | api | Free API keys; `time_series_point` (`kind=observation`) | B9, B13 |
| Securities filings | SEC EDGAR (Company Facts, submissions) | api | Transparent UA + contact; honor 10 req/s | B10 |
| Vulnerabilities | NVD (NIST), vendor security RSS | api + rss | Documented NVD rate limits respected | B10, B13 |
| Developer / research | GitHub (releases/advisories), arXiv | api | Free APIs; light pull | post-MVP / v1 |
| HTML / browser | (none in MVP) | html / browser | Rare, **approved** exception only; runtime robots check added in that connector's block | v2+ |

**Explicitly excluded in this build:** licensed market/news wires, paywalls, broker APIs, and social platforms. If a social signal is ever wanted, it must come through an aggregator vendor (so ToS/aggregation sit with the vendor), never through direct social-platform ingestion.

---

## 4. Build vs adopt

| Capability | Decision | Rationale |
|---|---|---|
| Relational + FTS + vector + time-series store | **Adopt** PostgreSQL 16 (+ `pgvector`, `pg_trgm`; optional Timescale) | One engine covers all four needs on one box; PG16 chosen for its support window (see ADR-0001). |
| Job queue / broker | **Build on Postgres** (`crawl_job` table, `SELECT … FOR UPDATE SKIP LOCKED`, DLQ via `status=dead`) | Avoids Redis/Celery/RabbitMQ/Kafka; durable, inspectable from the CLI. |
| Raw store | **Build** a content-addressed gzip store on local disk | Immutable evidence; blobs never go in Postgres. |
| Scheduler | **Adopt** APScheduler or cron, **exactly one instance** | Two instances double-fire; guarded by a single service + pg advisory lock. |
| Fetch / HTTP | **Adopt** httpx + feedparser | Standard, async-capable, conditional-request friendly. |
| Article extraction | **Adopt** trafilatura (newspaper4k/readability fallback) | Mature extraction; we own the parser-glue + golden fixtures. |
| Embeddings | **Build** a pluggable `EmbeddingBackend`; **adopt** sentence-transformers (local) / OpenAI / Google (cloud) | Operator currently lacks a powerful machine, so cloud is first-class; local stays an offline/privacy option (ADR-0006, ADR-0010). |
| Summaries / LLM | **Build** a pluggable `SummaryBackend`; **adopt** local or hosted LLMs, or `none` | Content-hash cache; hard monthly spend cap when cloud. |
| Search | **Adopt** Postgres FTS (`tsvector`); Meilisearch (single binary) only if outgrown | No OpenSearch. |
| MCP server | **Adopt** official MCP Python SDK / FastMCP over stdio | Read-only tools; provenance on every result. |
| CLI | **Adopt** Typer; entry point `atlas` | The only ingestion-management surface. |
| Deploy | **Adopt** Docker Compose on one VM (Hetzner default) | Cheap, single-box; nightly `pg_dump` + offsite raw sync. |

---

## 5. Architecture

### 5.1 Layers

- **Source Registry** — git-tracked YAML (canonical source of truth) mirrored into the Postgres `source` table: access method, policy status, rate limit, retention, freshness, parser/connector refs, schedule.
- **Scheduler** — APScheduler/cron, **exactly one instance**; computes due sources and enqueues `crawl_job` rows.
- **Job Queue** — one durable Postgres table (`crawl_job`) with a status machine and a `dead` DLQ status; claimed with `FOR UPDATE SKIP LOCKED`. No external broker.
- **Workers** — process pool claiming jobs: fetch (httpx/feedparser) → parse → enrich. Idempotent and observable. **Fetch role = exactly one worker** in MVP (per-host concurrency = 1); parse/enrich may scale out.
- **Raw Store** — immutable, gzipped, content-addressed payloads on local disk.
- **Normalized Store** — canonical records in PostgreSQL.
- **Time-Series** — RANGE-partitioned Postgres table (Timescale optional).
- **Search** — Postgres FTS (`tsvector`); `pg_trgm` for fuzzy entity-name lookups.
- **Vector** — `pgvector`, one active model+dim per deployment.
- **Enrichment** — language → NER → topics → embeddings → summaries.
- **Alerting** — rule-based, deduped, with evidence document IDs.
- **MCP Server** — read-oriented access to processed data over stdio.
- **CLI** — source management, manual crawls, health/digest, backups, purge. Not exposed to agents.

**Architecture rule:** MCP and agents depend on the *processed* stores; the ingestion/control plane must **not** depend on MCP. Agents never trigger crawls or change source policy. The operational health surface is **CLI-driven** (`atlas health`/`atlas digest`); the MCP `get_source_health` tool is a read-only convenience, not the operational gate.

### 5.2 Data flow (ASCII)

```text
                          git-tracked source YAML
                                   |
                                   v
        +------------------------------------------------+
        |  Source Registry (source table, default-deny)  |
        +------------------------------------------------+
                                   |
                                   v
                  +-----------------------------+
                  |  Scheduler  (EXACTLY ONE)   |  reads source.schedule (cron)
                  |  APScheduler/cron + pg lock |  enqueues crawl_job rows
                  +-----------------------------+
                                   |
                                   v
        +-------------------------------------------------------+
        |  crawl_job queue (Postgres)                           |
        |  FOR UPDATE SKIP LOCKED  ·  status machine  ·  DLQ     |
        +-------------------------------------------------------+
              |                  |                   |
       (fetch, 1 worker)   (parse, scale-out)  (enrich, scale-out)
              |                  |                   |
              v                  |                   |
   +--------------------+        |                   |
   | Fetcher (httpx)    |        |                   |
   | SSRF/URL-safety    |        |                   |
   | per-host rate limit|        |                   |
   | ETag/Last-Modified |        |                   |
   +--------------------+        |                   |
              |                  |                   |
              v                  |                   |
   +--------------------+        |                   |
   | Validation Gate    |        |                   |
   | MIME/size/status   |        |                   |
   +--------------------+        |                   |
              |                  |                   |
              v                  |                   |
   +-------------------------+   |                   |
   | Raw Store (disk)        |   |                   |
   | immutable, gzip,        |   |                   |
   | content-addressed       |   |                   |
   | -> raw_document row     |   |                   |
   +-------------------------+   |                   |
              |                  v                   |
              |        +----------------------+      |
              +------->| Parse (trafilatura)  |      |
                       | -> document (FTS)    |      |
                       | dedupe (URL + hash)  |      |
                       +----------------------+      |
                                  |                  v
                                  |     +--------------------------+
                                  +---->| Enrich: lang/NER/topics/ |
                                        | embeddings/summaries     |
                                        | -> entity/mention/       |
                                        |    embedding/summary     |
                                        +--------------------------+
                                                   |
                +----------------------------------+
                |                                  |
                v                                  v
   +------------------------+        +-----------------------------+
   | Freshness/health job   |        | Alert rules (deduped,       |
   | -> source_health       |        | evidence_doc_ids) -> alert  |
   +------------------------+        +-----------------------------+
                |                                  |
                v                                  |
   +------------------------+                      |
   | audit_log (append-only)|<---------------------+
   +------------------------+
                |
                v
   +-----------------------------+        +-----------------------------+
   | CLI (atlas)  CONTROL PLANE  |        | MCP server (stdio) READ-ONLY|
   | source mgmt, crawl, health, |        | 7 read tools, provenance,   |
   | digest, backup, purge       |        | result caps, sanitized text |
   +-----------------------------+        +-----------------------------+
   (control plane NEVER depends on MCP; agents never trigger crawls/policy)
```

---

## 6. Data models (overview)

Twelve canonical tables. **Full column-level detail and DDL notes live in `docs/schemas/README.md`.** Cross-cutting invariants: UTC `timestamptz` everywhere (source-local time stored separately when useful); sortable IDs (UUIDv7/ULID); `schema_version` on persisted records; `parser_version` on parsed output; `model_name`/`model_version` on enrichment output; `content_hash` on raw and parsed content; attribution on every externally sourced record; multi-table canonical writes in one transaction; raw blobs on disk, never in Postgres.

**Required Postgres extensions:** `pgvector` (vector search) and `pg_trgm` (fuzzy entity-name lookup); Timescale optional. An early migration runs `CREATE EXTENSION IF NOT EXISTS vector;` and `CREATE EXTENSION IF NOT EXISTS pg_trgm;`.

| # | Table | Purpose | Key points |
|---|---|---|---|
| 2.1 | `source` | Source registry (mirrors YAML) | `policy_status` default `unknown`; `rate_limit`/`retention`/`freshness` jsonb; **`config` jsonb** holds `policy.allowed_uses`, `policy.robots`, `fetch.connector`, `fetch.schedule` (cron), `fetch.timeout_seconds` so YAML round-trips losslessly and the scheduler can read per-source cron. `credential_ref` is text-or-null, **never** a secret. |
| 2.2 | `crawl_job` | Durable job queue + DLQ | `status` ∈ pending/claimed/running/done/failed/**dead**; unique `dedupe_key`; claimed via SKIP LOCKED; stale-claim reclaim by visibility timeout; 90-day retention. `last_error`/`payload` are sanitized before write (section 7). |
| 2.3 | `raw_document` | Immutable raw capture | unique `content_hash`; disk `path`; deletable **only** via the audited purge path. |
| 2.4 | `document` | **Current-state** canonical record | Last-write-wins on re-parse (R2): old parsed versions not retained but reproducible by re-running the pinned `parser_version` against immutable raw. unique `canonical_url` where not null; GIN tsvector on `body_text`; GIN on `topics`/`entity_ids`. **No `summary` column** — summaries live in 2.5. |
| 2.5 | `summary` | Cached LLM summaries w/ provenance | `scope` single/multi; `member_doc_ids[]`; cache key `unique(source_hash, model_name, model_version, prompt_version)`; **GIN(`member_doc_ids`)** so purge can find multi-doc summaries; powers `get_document_summary`. |
| 2.6 | `embedding` | Vector chunks | `unique(document_id, chunk_index, model_name, model_version)` (R13) — re-embedding an unchanged `source_hash` is a no-op; one active model+dim per deployment (R8); HNSW ANN index. |
| 2.7 | `time_series_point` | Macro observations + market bars | RANGE-partitioned on `ts` ⇒ **composite `PRIMARY KEY (id, ts)`** (R-correction 1; applies to any partitioned table); `UNIQUE NULLS NOT DISTINCT (provider, series_id, ts, vintage)` (R1, needs PG15+; pre-15 fallback `vintage_key = coalesce(vintage,'epoch')`). |
| 2.8 | `entity` | Canonical entities | `aliases[]`, `identifiers` jsonb (CIK/LEI/ticker/wikidata); `merged_into` history; trigram index `USING gin (canonical_name gin_trgm_ops)`. |
| 2.9 | `mention` | Entity mentions in documents | `confidence`, optional `sentiment`, `context_snippet`; powers `get_entity_mentions`. |
| 2.10 | `alert` | Rule-based alerts | unique `dedupe_key`; `evidence_doc_ids[]`; lifecycle open→acknowledged→resolved, `purged` when evidence erased; 6–12mo retention. |
| 2.11 | `audit_log` | Append-only audit | **`retention_class`** ∈ purge/routine (R14): routine rotates after a few months, **purge entries retained** so erasure stays provable. `detail` sanitized before write. |
| 2.12 | `source_health` | Freshness/health snapshot | computed by the freshness job; `status` ok/warn/stale/error/cooling_down; powers `get_source_health` and the daily digest. |

**Referential-integrity choice (R17):** FKs use **`ON DELETE NO ACTION`/RESTRICT** so the app-driven purge ordering is DB-enforced (prevents an accidental cascade from over-deleting on entity purge). The array-of-id columns (`document.entity_ids`, `document.topics`, `alert.evidence_doc_ids`, `summary.member_doc_ids`) are **intentionally non-referential** and reconciled only by the purge job.

---

## 7. Storage

- **Raw payloads:** local disk, immutable, gzipped, content-addressed. Encrypt only if the host is shared/untrusted.
- **Normalized records and control plane:** PostgreSQL 16.
- **Time-series:** RANGE-partitioned Postgres tables (monthly/yearly), optional LIST sub-partition by provider; Timescale hypertable optional.
- **Full-text:** Postgres `tsvector`; Meilisearch only if outgrown.
- **Embeddings:** `pgvector` (store model + dim + version); HNSW index; one active dim per deployment.
- **Entity edges:** Postgres rows. A graph DB requires a documented ADR.
- **Audit:** append-only Postgres table with `retention_class`.
- **Secrets:** `.env` (gitignored) → environment variables. **SOPS is OUT OF SCOPE for MVP (ADR-0011): secrets are `.env`-only.** If/when an `age`/SOPS-encrypted file is adopted post-MVP, the decrypt key lives **outside** the repo (password manager / hardware key) with a documented `SOPS_AGE_KEY_FILE` hint and a `sops -d … > .env` deploy runbook step. Source YAML contains no secrets and no private endpoints. Secrets are never printed/logged.
- **Backups:** nightly `pg_dump` + `rclone`/`rsync` of the raw dir offsite; test a restore (B14 restore drill).

**Secret-leak hardening (R-correction 8):** before persisting `crawl_job.last_error`, `crawl_job.payload`, or `audit_log.detail`, a single `sanitize_for_storage` helper in `packages/security/secrets` redacts known-secret patterns and strips URL query/userinfo plus `Authorization`/`Cookie` headers. Storing resolved credential values or credential-bearing URLs in any jsonb/text column is forbidden — **backups inherit this**. (FRED uses `?api_key=…`, so this is concrete, not hypothetical.) The B15 secrets-in-logs regression test covers `last_error` and `audit_log.detail`, not just stdout.

Retention is source/data-class specific: raw 1–2y then prune; normalized kept; routine audit rotates but purge-records are retained; alerts 6–12mo. Public does not mean indefinitely retainable.

---

## 8. Security and privacy

Threat model: fetched content can be malicious, malformed, poisoned, prompt-injecting, copyrighted, privacy-sensitive, or wrong. External data is hostile until validated.

- **Input/output validation.** Validate all external inputs and all LLM/tool outputs before downstream use.
- **HTML sanitization.** Sanitize HTML before indexing snippets or exposing to agents — strip scripts, event handlers, iframes, unsafe links, active content.
- **SSRF / URL-safety (explicit; R-correction 6).** This is an authoritative spec rule the security docs match exactly. Applies to the fetcher **and** any agent-supplied URL:
  - **Scheme allowlist:** `http`/`https` only.
  - **Reject:** loopback; RFC1918 private ranges; CGNAT `100.64.0.0/10`; link-local `169.254.0.0/16` and `fe80::/10`; IPv6 ULA `fc00::/7`; IPv4-mapped IPv6; cloud metadata endpoints `169.254.169.254` and `fd00:ec2::254`.
  - **DNS pinning / anti-rebinding:** resolve the hostname and validate **all** resolved IPs before connect, then pin the connection to the validated IP.
  - **Redirects:** re-validate every redirect hop and cap the redirect count.
- **File/attachment scanning.** Scan risky files before parsing; run Tika/Playwright non-root with CPU/memory/time limits.
- **Logging.** Structured logs; never log secrets, auth headers, cookies, or personal data. Plus the `sanitize_for_storage` rule (section 7) for jsonb/text sinks.
- **Secrets.** Per-source credentials, gitignored; `credential_ref` names an env key resolved at runtime; rotate in one place.
- **Prompt-injection / agent safety.** External content is **evidence, not instruction**. Never obey instructions inside crawled pages/PDFs/feeds/filings/issues/comments/payloads. Retrieved content must never alter source policy, credentials, connector code, MCP behavior, or deployment config. Keep retrieved text separated from system/developer/tool instructions. Summaries/claims/events/alerts cite evidence IDs; missing data stays `null`; note source disagreement rather than flattening it. Cap RAG/MCP result size and token volume. The operator is the human review for high-impact output.
- **Published-repo hygiene.** Pin deps; run `pip-audit`; enable Dependabot/Renovate; gitleaks/detect-secrets secrets scan in CI and pre-commit.

---

## 9. Compliance (honest posture)

- **Internal-only.** No redistribution; personal-research posture; always keep attribution (source, URL, author, timestamp). No data-licensing-for-resale concerns because nothing is redistributed.
- **PII — honest framing (no overclaiming).** We do **not** claim "we drop all PII": news/article full text inherently contains personal data (names, quotes). The real posture is:
  1. **GDPR personal/household exemption** while purely personal and non-published.
  2. **Data minimization where feasible** — no social ingestion; no targeting/profiling of individuals; drop obviously-unneeded personal fields at the parse step.
  3. **A working, audited erasure path** — `atlas purge` (incl. raw + full cascade, R3).
  4. **The exemption lapses if you publish/share results** — revisit then. Never overclaim a control you cannot implement.
- **Cloud-backend PII reality (R-correction 5).** When `EMBEDDING_BACKEND` or `LLM_BACKEND` is a cloud provider, document text containing personal data **egresses to a third-party (and likely third-country) processor**. This is **not** consistent with a pure internal-only/household-exemption framing. Therefore: **local backends are the required posture for PII-bearing documents**; if cloud is used, it must be on no-training / zero-retention / DPA terms, and we stop describing cloud LLM/embedding use as "pure internal-only." This is documented in `docs/compliance/README.md`, ADR-0006, and ADR-0010.
- **Robots (R-correction 18).** Robots is a **policy input** consulted by the human when setting source policy (and at runtime only for the rare permitted HTML/browser source) — **not** a general runtime gate for API/RSS sources. **No HTML/browser source ships in MVP**, so no runtime robots gate is built in B1–B15; the `robots` field is advisory until an HTML source is approved, at which point a runtime robots check is added in that connector's block.
- **Retention classes.** Raw 1–2y then prune; normalized kept; routine audit rotates but **purge-records retained**; alerts 6–12mo.
- **FOSS boundary.** Code is published under Apache-2.0; never publish harvested data, operator configuration, credentials, raw stores, backups, private endpoints, or licensed feeds (ADR 0040).
- **Do not touch** licensed market/news wires, paywalls, broker APIs, or social platforms.

**Purge cascade (R3, with R-corrections 4 and 17).** `atlas purge` is the **one** documented exception to raw immutability and is idempotent and audited.
- *Given `doc_id`:* delete `embedding`, `mention`, and **`summary`** rows referencing it — where `summary` matching is `document_id = doc_id OR doc_id = ANY(member_doc_ids)` (uses the GIN on `member_doc_ids`), so multi-doc summaries that set `document_id=null` are not missed; recompute or hard-delete affected multi-doc summaries. Remove the doc from FTS; delete the `document` row; delete the `raw_document` file + row; scan `alert.evidence_doc_ids[]` and remove the id (an alert losing **all** evidence → `status=purged`); write a **retained** `audit_log` entry (`retention_class='purge'`) recording action + hashes — **never** the personal data.
- *Given `entity_id`:* delete the `entity` + its `mention` rows; documents survive.
- A **purge regression test covers a multi-doc summary** to prove erasure reaches `summary_text`/`citations`.

---

## 10. Reliability, concurrency, and the multi-worker model

Goal: runs unattended for weeks and tells you when something breaks.

### 10.1 Job reliability

- Idempotent, restartable jobs with deterministic `dedupe_key`; content-hash upserts; `unique(source_id, external_id/guid)` so replays never duplicate.
- Exponential backoff with jitter on transient failures (DNS/connect/read/408/429/5xx); respect `Retry-After`; do not retry most 4xx.
- Circuit-break a source after N consecutive failures (`source_health.status=cooling_down`) and surface it in the digest.
- DLQ: jobs exceeding `max_attempts` move to `status=dead`, inspectable via CLI.
- Data-quality checks: timestamp sanity, null rates, impossible values, duplicate spikes, schema drift.
- Graceful degradation: search works without enrichment; FTS works if vector search is down.
- Reindex/backfill is bounded, observable, resumable; migrations documented and safe.

### 10.2 Concurrency model (SPEC 5 — document prominently)

- **Scheduler: EXACTLY ONE instance.** Two instances double-fire. Guard via a single systemd unit / single compose service and a **pg advisory lock acquired at startup**. Stated as an invariant in architecture + runbooks.
- **Workers** claim jobs from `crawl_job` via `SELECT … FOR UPDATE SKIP LOCKED`.
- **Fetch concurrency per host (R5):** MVP = **exactly one fetch worker** (per-host fetch concurrency = 1) with in-process per-host token buckets. Parse/enrich can scale out. **Fetch-role startup guard (R-correction 16):** a pg advisory lock (or a `FETCH_WORKERS=1` assertion) refuses to start a second fetch worker until the documented scale-up path is enabled — otherwise the "SEC EDGAR 10 req/s true across workers" guarantee silently breaks.
- **Scale-up path (documented, when >1 fetch worker):** a Postgres-backed per-host token bucket (table `host_rate_state`) or `pg_advisory_xact_lock(hashtext(host))` + decrement-on-claim. Always respect `Retry-After`.
- **Crash safety:** claimed-but-unfinished jobs (`claimed_at` older than the visibility timeout) are reclaimed to `pending`.

### 10.3 Operational health surface (R-corrections 10, 11)

- `audit_log` and the result-cap discipline ship **with** the MCP search tool (B7).
- `source_health` + the freshness job + `get_source_health` ship **together** in B8 (B7 has no health table to read yet — explicit build-order dependency).
- **`atlas health` / `atlas digest` CLI command (B8):** reads `source_health` + `crawl_job` DLQ depth, runs a scheduler/worker liveness check, and emits the daily digest via the alert delivery channel. **Health is CLI-driven (control plane, MCP-independent).** The MCP `get_source_health` tool is a read-only convenience, not the operational gate (the architecture rule forbids the control plane depending on MCP).

### 10.4 Cross-session coordination (humans + AI sessions over time)

`docs/STATUS.md` is the shared board. Protocol: before starting, read `CLAUDE.md` + `docs/STATUS.md`; **claim** a work-area by adding a dated entry under "In progress" (area + intent + who); at close, update Done/Next/Known issues, record user-facing changes in `CHANGELOG.md` `[Unreleased]`, add one ADR per architecture-changing decision, keep diffs minimal and reversible.

---

## 11. Intelligence / enrichment

Enrichment runs language → NER → topics → embeddings → summaries. Deduplicate **before** embedding or summarizing; never embed boilerplate/nav/duplicated syndicated copies.

### 11.1 Embeddings (pluggable, local OR cloud — SPEC 6)

- `EmbeddingBackend` interface in `packages/enrichment/embeddings` with implementations: `local` (sentence-transformers), `openai` (text-embedding-3-small/large), `google` (text-embedding-004/gecko), plus a deterministic test stub.
- Config: `EMBEDDING_BACKEND=local|openai|google`, `EMBEDDING_MODEL`, `EMBEDDING_DIM`, `EMBEDDING_API_KEY` (cloud). Default dev: `local`.
- The operator currently lacks a powerful machine, so **cloud embeddings are a first-class supported option**; local remains the option for offline/privacy and is the **required** posture for PII-bearing docs (section 9).
- **Dim is fixed per deployment (R8):** `pgvector vector(N)` plus an ANN index require one dimension. Switching backends/dims is a **documented, bounded, resumable backfill + reindex** (runbook), not an online change. `dim` + `model_version` are stored for provenance and mismatch detection.
- **Idempotency (R13):** `unique(document_id, chunk_index, model_name, model_version)`; re-embedding an unchanged `source_hash` is a no-op (skip), mirroring the summary cache rule.

### 11.2 Summaries pipeline (SPEC 6)

- `SummaryBackend` / LLM interface in `packages/enrichment/summaries`. Config: `LLM_BACKEND=local|api|none`, `LLM_MODEL`, `LLM_API_KEY` (api), `LLM_MONTHLY_SPEND_CAP_EUR`.
- `api` supports OpenAI/Google/Anthropic-style hosted models. `none` ⇒ `get_document_summary` returns only cached summaries, else `null` with a reason.
- **Cache (R4):** the dedicated `summary` table keyed on `unique(source_hash, model_name, model_version, prompt_version)`. Dedupe-before-summarize; **never** re-summarize an identical (content, model, prompt) tuple. A member `content_hash` change changes `source_hash` ⇒ cache miss ⇒ recompute.
- Input docs are treated as **untrusted evidence**. A **hard monthly spend cap** applies when `api` is used. `prompt_version` is tracked. Every embedding/summary row records `model_name` + `model_version` (+ `dim` for embeddings, `prompt_version` for summaries).

---

## 12. Alerting

Three rule-based alerts in MVP, each with dedupe + `evidence_doc_ids`, delivered via webhook/ntfy/email, and queryable through the `get_alerts` MCP tool:

| Rule | `alert_type` | Trigger source |
|---|---|---|
| Macro release | `macro_release` | FRED in MVP (ECB/Eurostat in v1) |
| CVE watch | `cve_watch` | NVD + vendor security RSS |
| SEC filing | `sec_filing` | SEC EDGAR |

Delivery channel is configured via `ALERT_WEBHOOK_URL` (or `NTFY_TOPIC` / `SMTP_*`). The same delivery channel carries the daily digest (section 10.3). Alerts cite evidence IDs and never invent facts; when an alert loses all evidence to a purge, its status becomes `purged`.

---

## 13. MVP scope — single canonical ordered backlog (B1–B15)

This is the **one** authoritative backlog. It replaces the three older conflicting lists. Discipline: the MCP MVP tool set is **all 7 tools** (section 14); freeze scope until the current set runs unattended for a month before adding sources/services.

| Block | Deliverable | Done when |
|---|---|---|
| **B1** | Repo + monorepo skeleton + Docker Compose (Postgres 16 + pgvector + pg_trgm) + CI + pre-commit + Alembic harness + `source` schema; **finalize Python import/packaging layout** (src vs flat; how `apps/`+`packages/` map into the `atlascore` distribution; exact module path behind the `atlas` console script). | `make test` green; migrations run. |
| **B2** | Source registry + default-deny policy + canonical source YAML (retention + freshness) + CLI `source validate`/`source test`. | Register an `approved` RSS source; `unknown`/`pending`/`prohibited` refused. |
| **B3** | Raw store (immutable, content-addressed, gzip) + `crawl_job` queue (SKIP LOCKED + DLQ). | Jobs claim/run/retry/dead; raw on disk. |
| **B4** | RSS connector (ETag/Last-Modified) + fetcher (SSRF/URL-safety + per-host rate limiting). | RSS items fetched, raw captured, duplicate GUIDs ignored. |
| **B5** | Article parser (trafilatura + fallback) → canonical `document` + content hashing + dedupe (URL + hash). | Documents created; parser golden fixtures in CI. |
| **B6** | Postgres FTS index + CLI `search`. | Search by text/date/source from the CLI. |
| **B7** | MCP server + `search_documents` + **`audit_log` table** + result caps. | Agent answers with citations over stdio; searches are audit-logged. |
| **B8** | `source_health` + freshness job + `get_source_health` MCP tool + **`atlas health`/`atlas digest` CLI** + daily digest. | A stale source appears in the digest and in `source_health`; `get_source_health` reads it. |
| **B9** | FRED connector + `time_series_point` + `get_macro_series`. | Query a macro series via MCP. |
| **B10** | SEC EDGAR (basic) + NVD connectors (documented rate limits respected). | Filings + CVEs searchable. |
| **B11** | Enrichment: language → NER → topics → embeddings (`pgvector`; `EmbeddingBackend` local or cloud) + `entity`/`mention` + `get_entity_mentions`. | Entity mentions queryable; re-runs are idempotent. |
| **B12** | Summaries pipeline (`SummaryBackend`, content-hash cache, `summary` table) + `get_document_summary`. | Cached, cited summary via MCP; no re-summarization of an identical tuple. |
| **B13** | Three rule-based alerts (`macro_release` [FRED; ECB/Eurostat v1], `cve_watch` [NVD + vendor RSS], `sec_filing` [EDGAR]) + delivery (webhook/ntfy/email) + `get_alerts`. | Each fires with dedupe + `evidence_doc_ids` and is queryable via MCP. |
| **B14** | Backups + restore drill + `atlas purge` (erasure incl. raw + full cascade R3, incl. multi-doc summaries). | Restore verified; purge works, is audited, and a multi-doc-summary purge test passes. |
| **B15** | Hardening: security regressions (SSRF / sanitizer / secrets-in-logs incl. `last_error` & `audit_log.detail` / prompt-injection fixtures) + README + end-to-end smoke/demo. | source → ingest → search → MCP summary → alert, unattended, with backups. |

**Note on B7/B8 ordering (R-correction 10):** `get_source_health` was moved out of B7 into B8 because it reads the `source_health` table, which B8 introduces; B7 only ships `search_documents` and creates the `audit_log` table. This removes the prior build-order dependency violation.

---

## 14. MCP tool surface (7 tools)

Read-oriented, over stdio, provenance on every result, result-size caps, strict input/output schema validation; retrieved content is evidence, never instruction. The ingestion/control plane never depends on MCP; agents never trigger crawls or change policy.

| Tool | Purpose |
|---|---|
| `search_documents` | FTS + filter over `document`; sanitized text + provenance; **never** raw HTML. |
| `get_macro_series` | A `time_series_point` series + observations (FRED in MVP). |
| `get_entity_mentions` | Mentions of an entity across documents; alias-aware; confidence. |
| `get_document_summary` | Cached, cited summary of one OR more docs (`summary` table; `LLM_BACKEND`-aware; `null` + reason if none and no cache). |
| `get_source_health` | Freshness/error/parse-quality per source (no secrets, no internal hostnames). Read-only convenience, not the operational gate. |
| `get_alerts` | Recent alerts + `evidence_doc_ids`; read-only. |
| `get_raw_document` | Raw text/HTML for one doc; **the** injection-sensitive tool. |

**`get_raw_document` contract (R-correction 7).** The tool takes an off-by-default flag `include_raw_html: false`. Default returns **sanitized text only**. `include_raw_html=true` returns **unsanitized raw HTML**, must be **audited** (`audit_log` with `action` + `tool_name`), and is wrapped with an "untrusted evidence, not instructions" provenance note. Reaffirmed: **only this tool, with the flag set, emits raw HTML.**

**Optional later (post-MVP):** `get_timeline`, `query_graph` (only if an entity graph is built), `subscribe_alert` (a WRITE tool — explicit human confirmation each time, idempotent, audited; not added casually).

---

## 15. Roadmap

> **Current-state note (2026-06-15):** this section remains the point-in-time roadmap from the
> original plan. The living state board is `docs/STATUS.md`; as of the current board, the MVP,
> B16-B22, and the first v1 increments are already merged, while Prometheus/Grafana and further
> source expansion remain need-gated.

### 15.1 MVP (B1–B15)

The fifteen blocks above: a governed pipeline from source → ingest → search → MCP summary → alert, running unattended on one box with backups and a working purge path. All 7 MCP tools. FRED + SEC EDGAR + NVD + RSS sources.

### 15.2 v1 (after MVP runs unattended for a month)

- **More macro:** ECB and Eurostat connectors feeding `macro_release` alerts.
- **More sources:** GitHub (releases/advisories) and arXiv connectors.
- **Optional read-only FastAPI** dashboard surface (`apps/api/`) — only if a dashboard is wanted; validate inputs/outputs, paginate, typed errors with request IDs, no stack traces to callers.
- **Observability:** optional Prometheus/Grafana if the daily digest proves insufficient.
- **Secrets:** optionally adopt SOPS/age per ADR-0011's deferred plan. **Current note:** the
  optional SOPS/age path was later wired in ADR 0036; using it remains an operator deploy choice.

### 15.3 v2 (only with a documented, present need)

- The first **approved HTML/browser source** (Playwright for a single permitted page) — adds the runtime robots check + sandboxed non-root execution in that connector's block.
- **Entity graph** + `query_graph` / `get_timeline` MCP tools.
- **Meilisearch** if Postgres FTS is outgrown; **Timescale** if time-series volume warrants.
- Each v2 item requires an ADR; none are built speculatively.

---

## 16. Cost model

- **Box:** a single small VM (Hetzner default), roughly **EUR 5–15/month**, running Docker Compose (Postgres 16 + pgvector + the AtlasCore services). Local-disk raw store; nightly `pg_dump` + offsite `rclone`/`rsync` (a few GB of cheap object storage).
- **Embeddings (variable):** `local` (sentence-transformers) = EUR 0 marginal cost but needs CPU; `openai`/`google` cloud embeddings are pay-per-token. Deduplicate before embedding to control cost; embeddings are a no-op on unchanged content.
- **LLM summaries (variable):** `none`/`local` = no cloud spend; `api` is pay-per-token and governed by a **hard monthly cap** (`LLM_MONTHLY_SPEND_CAP_EUR`). Cache summaries on content hash; never re-summarize an identical tuple.
- **Cost discipline (SPEC 20):** prefer APIs/RSS/ETags/conditional requests over fetching; prefer local models for routine enrichment; cap MCP result sizes; lifecycle/prune low-value raw after 1–2y.

Net: a predictable single-digit-EUR fixed cost plus a capped, controllable variable cloud-AI cost.

---

## 17. Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **Over-scope** (the #1 risk) | High | High | Single ordered backlog (B1–B15); non-goals list; freeze scope until current set runs unattended a month; ADR required for any new service/source. |
| Compliance drift (PII / publishing) | Medium | High | Honest PII framing (section 9); local backends required for PII docs; working audited `atlas purge`; revisit exemption if results are published. |
| Cloud-AI PII egress | Medium | High | Local default for PII; cloud only on no-training/zero-retention/DPA terms; documented, not hand-waved (ADR-0006/0010). |
| Secret leakage into DB/backups | Medium | High | `sanitize_for_storage` on `last_error`/`payload`/`audit_log.detail`; `credential_ref` never a secret; B15 regression test on DB sinks; gitleaks/detect-secrets in CI + pre-commit. |
| SSRF via fetcher or agent URL | Medium | High | Explicit SSRF spec (section 8): scheme allowlist, IP-range rejection, DNS pin/anti-rebind, per-hop redirect validation; SSRF regression fixtures in B15. |
| Prompt injection from fetched content | Medium | Medium | Evidence-not-instruction posture; sanitized-by-default `get_raw_document`; injection fixtures in B15. |
| Rate-limit breach (esp. SEC EDGAR 10 req/s) | Medium | Medium | Single fetch worker + per-host token bucket; fetch-role startup guard; respect `Retry-After`; documented scale-up path. |
| Double-firing scheduler | Low | Medium | Exactly one scheduler instance + pg advisory startup lock. |
| Partitioned-table DDL failure | Low | High (build-blocking) | Composite `PRIMARY KEY (id, ts)` on partitioned tables; documented as a rule for any future partitioned table. |
| Backup/restore failure | Low | High | Nightly `pg_dump` + offsite raw sync; tested restore drill in B14. |
| Source drift / silent staleness | Medium | Medium | `source_health` + freshness job + circuit breaker + daily digest; per-source freshness thresholds. |

---

## 18. Concrete tech recommendations

- **Language/tooling:** Python 3.12+, `uv`, `pyproject.toml` with locked deps, `ruff` (format + lint), `mypy` (or `pyright`), `pytest` (+ `respx`, `vcrpy`).
- **Data layer:** PostgreSQL 16, `pgvector` (HNSW), `pg_trgm`; SQLAlchemy 2.x + Alembic; Pydantic v2 at boundaries; `Decimal` for money; timezone-aware UTC.
- **Fetch/parse:** httpx, feedparser, trafilatura (newspaper4k/readability fallback); Apache Tika (sandboxed) only when ingesting PDFs/filings as files; Playwright only for a single permitted page (v2+) — no Scrapy/Selenium/browser fleet.
- **Queue/scheduler:** Postgres `crawl_job` (SKIP LOCKED) + APScheduler/cron (one instance). No Redis/Celery/RabbitMQ/Kafka.
- **Enrichment:** spaCy small model + rules for NER/topics; local sentence-transformers or cloud embeddings (pluggable); local or hosted LLM for summaries (pluggable, capped).
- **MCP/CLI:** official MCP Python SDK / FastMCP (stdio); Typer CLI (`atlas`).
- **Deploy/ops:** Docker Compose on one VM; nightly `pg_dump` + `rclone`/`rsync`; healthcheck + daily digest for observability.
- **CI/hygiene:** `.github/workflows/ci.yml` runs ruff format-check, ruff lint, mypy, pytest (unit+contract, no live network), `pip-audit`, an Alembic migration check, source-YAML schema validation, and a gitleaks/detect-secrets scan on a Python 3.12 matrix; optional integration job with a Postgres 16 + pgvector service container. pre-commit mirrors the fast checks. Dependabot for pip + github-actions, weekly.

---

## 19. Remaining decisions (all resolved → ADRs)

All major decisions are resolved and recorded as **Accepted** ADRs dated 2026-06-03 in `docs/decisions/` (Context / Decision / Consequences format). Index in `docs/decisions/README.md`.

| Decision | Resolution | ADR |
|---|---|---|
| Postgres 16 baseline + extensions | PG16 (for support window) with `pgvector` + `pg_trgm`; Timescale optional; `NULLS NOT DISTINCT` needs PG15+ with a pre-15 `vintage_key` fallback. | ADR-0001 |
| Postgres-backed job queue (no broker) | `crawl_job` table, SKIP LOCKED, `dead` DLQ. | ADR-0002 |
| Content-addressed disk raw store | Immutable gzip on local disk; blobs never in Postgres. | ADR-0003 |
| `document` re-parse versioning (R2) | Current-state, last-write-wins, reproducible-not-retained; no parsed-version table. | ADR-0004 |
| `time_series_point` shape (R1 + composite PK) | One unified table; `UNIQUE NULLS NOT DISTINCT (provider, series_id, ts, vintage)`; RANGE partition on `ts` with composite `PRIMARY KEY (id, ts)`. | ADR-0005 |
| Pluggable embedding/LLM backends (R8) + cloud PII | Local OR cloud; one active dim per deployment; local required for PII-bearing docs; cloud only on no-training/zero-retention/DPA terms. | ADR-0006 |
| Summary cache + provenance (R4) | Dedicated `summary` table; content-hash cache key; dedupe-before-summarize. | ADR-0007 |
| Concurrency / single-instance guards (R5, R16) | Exactly one scheduler + one fetch worker, both pg-advisory-lock guarded; documented Postgres-token-bucket scale-up. | ADR-0008 |
| Purge cascade incl. multi-doc summaries (R3, R-corr 4, R17) | Full audited cascade; summary match via `member_doc_ids[]`; FKs `ON DELETE NO ACTION`; arrays non-referential; retained purge audit. | ADR-0009 |
| `get_raw_document` injection contract (R7) + sanitize-for-storage (R8/corr 8) | Off-by-default `include_raw_html`; audited; single SSRF spec; `sanitize_for_storage` on DB text/jsonb sinks. | ADR-0010 |
| Secrets posture (R15) | SOPS out of scope for MVP; `.env`-only (gitignored); deferred SOPS plan documented. | ADR-0011 |

---

*End of AtlasCore Project Plan. See `docs/STATUS.md` for current build state (starts at B1, not-started, dated 2026-06-03).*
