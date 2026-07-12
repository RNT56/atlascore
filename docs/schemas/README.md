# AtlasCore Data Model Reference

> **Authoritative source:** `ATLASCORE CANONICAL DECISIONS SPEC v1` (dated 2026-06-03). This document is the deep-dive reference for the canonical data model and MUST agree with the spec exactly. Where this file overlaps `CLAUDE.md`, `docs/PROJECT-PLAN.md`, the migration files under `migrations/`, or the Pydantic models in `packages/schemas/`, the spec wins.
>
> **Date:** 2026-06-03

---

## 1. Engine & Extensions

| Item | Decision |
|------|----------|
| **Engine** | **PostgreSQL 16** (chosen baseline). |
| **Why PG16 (not the feature myth)** | PG16 is chosen on its **support-window merits** (long lifetime ahead of us), not because of `NULLS NOT DISTINCT`. **`NULLS NOT DISTINCT` was introduced in PostgreSQL 15+, not 16.** If the deployment is ever pinned to `<15`, use the documented fallback `vintage_key = coalesce(vintage,'epoch')` inside the unique index (see [§7 time_series_point](#27-time_series_point)). |
| **Required extensions** | `pgvector` (vector search), `pg_trgm` (trigram similarity for entity name search). |
| **Optional extensions** | Timescale (hypertables for `time_series_point`). |
| **Raw blobs** | On **local disk only**, content-addressed, gzipped. **Never** stored in Postgres. |

An **early migration** must run, before any index that depends on them:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
-- Timescale optional, only if hypertables are wanted:
-- CREATE EXTENSION IF NOT EXISTS timescaledb;
```

`pg_trgm` is **mandatory** because `entity.canonical_name` uses a trigram GIN index (`gin_trgm_ops`); without the extension the index and similarity queries cannot be created.

---

## 2. Global Invariants

These hold for **every** table unless stated otherwise. The Pydantic models in `packages/schemas/` are the source of truth; Alembic migrations under `migrations/` are generated to match.

1. **UTC everywhere.** All timestamps are `timestamptz` and stored in UTC. When source-local time is meaningful (e.g. a filing's local publish time), keep it in a **separate** column (`source_local_published_at`), never by mutating the canonical UTC value.
2. **Sortable IDs.** Primary keys are **UUIDv7 or ULID** (time-sortable), stored as `uuid` / `text`. New rows sort in creation order without a separate sequence.
3. **Versioning columns.**
   - `schema_version` on every **persisted** record.
   - `parser_version` on **parsed** output (`document`).
   - `model_name` + `model_version` on **enrichment** output (`embedding`, `mention`); `+ dim` for embeddings; `+ prompt_version` for `summary`.
4. **Content hashing.** `content_hash` on **raw** content (`raw_document`) and on **parsed** content (`document`). Drives dedupe and cache invalidation.
5. **Attribution.** Every externally sourced record carries provenance back to `source` (source, URL, author, timestamp). `credential_ref` columns name an env key; **never** a secret value.
6. **Transactional multi-table writes.** A canonical write that touches multiple tables (e.g. `raw_document` + `crawl_job` update, or `document` + `embedding` + `mention`) happens in **one transaction**.
7. **Immutability of raw.** `raw_document` rows + on-disk blobs are immutable except via the single audited `atlas purge` path ([R3](#purge-cascade-r3)).
8. **Default-deny policy.** A `source` is only fetched when `policy_status = approved` (or `restricted` with encoded constraints). Missing/`unknown`/`pending`/`prohibited` → disabled.
9. **Foreign-key ON DELETE = NO ACTION / RESTRICT** ([R17](#fk-on-delete-r17)). Delete ordering is **application-driven** by the purge job and **DB-enforced** by RESTRICT. Array-of-id columns (`document.topics`, `document.entity_ids`, `alert.evidence_doc_ids`, `summary.member_doc_ids`) are **intentionally non-referential** (no FK) and are reconciled only by the purge job.

---

## 3. Schema Resolutions Index

The data model encodes these explicit, traceable resolutions from the spec:

| ID | Resolution | Where |
|----|------------|-------|
| **R1** | `time_series_point` idempotent upsert via `UNIQUE NULLS NOT DISTINCT (provider, series_id, ts, vintage)`; PG15+ feature; pre-15 `vintage_key` fallback. | [§7](#27-time_series_point) |
| **R2** | `document` holds **current state only**; re-parse is last-write-wins; old parsed versions are **reproducible (re-run pinned `parser_version` against immutable raw), not retained**; no versioned parsed-doc table. | [§4](#24-document) |
| **R3** | Immutability has **one** exception — the audited `atlas purge` path; full cascade incl. `summary.member_doc_ids[]` scan. | [Purge cascade](#purge-cascade-r3) |
| **R4** | `summary` is a dedicated table with a **content-hash cache key**; dedupe-before-summarize; never re-summarize an identical tuple. | [§5](#25-summary) |
| **R8** | **One active embedding model + dim per deployment** (config-set); switching dims = bounded backfill + reindex runbook. | [§6](#26-embedding) |
| **R13** | `embedding` idempotency: `unique(document_id, chunk_index, model_name, model_version)`; unchanged `source_hash` re-embed is a no-op. | [§6](#26-embedding) |
| **R14** | `audit_log.retention_class` distinguishes `purge` (retained) from `routine` (rotated). | [§11](#211-audit_log) |
| **R17** | FK `ON DELETE NO ACTION/RESTRICT`; array-id columns non-referential. | [§2](#2-global-invariants) |
| **Partition PK** | Partitioned-by-`ts` tables require a **composite** PK `(id, ts)` — Postgres rejects a PK/unique constraint that omits a partition-key column. Applies to any future partitioned table. | [§7](#27-time_series_point) |

---

## 4. The 12 Tables

The canonical set is exactly **12 tables**: `source`, `crawl_job`, `raw_document`, `document`, `summary`, `embedding`, `time_series_point`, `entity`, `mention`, `alert`, `audit_log`, `source_health`.

---

### 2.1 source

**Purpose.** The source registry. YAML (git-tracked, [§ Source Registry shapes](#source-registry-yaml)) is the source of truth and is mirrored into this table. Default-deny: only `approved`/`restricted` sources are fetched.

**Columns.**

| Column | Type | Notes |
|--------|------|-------|
| `id` | `uuid` (UUIDv7) | PK |
| `name` | `text` | **unique** |
| `category` | `text` | e.g. `finance_filings`, `cve`, `macro` |
| `access_method` | `text` | `api` \| `rss` \| `html` \| `browser` |
| `base_url` | `text` | |
| `policy_status` | `text` | `approved`\|`restricted`\|`pending`\|`unknown`\|`prohibited`; **default `unknown`** |
| `rate_limit` | `jsonb` | `{max_requests, window_seconds}` |
| `retention` | `jsonb` | `{raw, normalized, audit?}` — structured, per data-class ([R6](#source-registry-yaml)) |
| `freshness` | `jsonb` | `{target, warn_after, error_after}` ([R7](#source-registry-yaml)) |
| `config` | `jsonb` | **Round-trip home** ([R9](#r9-source-table-round-trips-the-yaml)) for `policy.allowed_uses`, `policy.robots`, `fetch.connector` (dotted path:Class), `fetch.schedule` (cron), `fetch.timeout_seconds`. The scheduler reads `config->>'schedule'` for the per-source cron. |
| `checkpoint` | `jsonb` or `null` | **Runtime-owned incremental crawl cursor (B20, migration `0011`).** NVD's `lastModStartDate`/paging, SEC's newest `filingDate`/accession. Written ONLY by the worker after a successful fetch (`storage.checkpoints`) — **never** by the YAML loader, so `atlas source sync` can't clobber crawl progress. NULL = no cursor yet (full fetch). Distinct from `config` for exactly this reason. ([ADR 0031](../decisions/0031-b20-connector-depth-headers-keyed-nvd-checkpoint.md)) |
| `parser_ref` | `text` | parser module ref (a parser, **not** the fetch connector) |
| `credential_ref` | `text` or `null` | names an env key resolved at runtime; **NEVER a secret value** |
| `enabled` | `bool` | |
| `schema_version` | `int` | |
| `created_at` | `timestamptz` | |
| `updated_at` | `timestamptz` | |

> **R9 — round-trip.** The section-4 YAML carries `policy.allowed_uses`, `policy.robots`, `fetch.connector`, `fetch.schedule`, and `fetch.timeout_seconds`. None of these map to `parser_ref` (a parser). They live in the `config jsonb` column so the source round-trips losslessly and the scheduler has a place to read each source's cron schedule.

**Indexes.** `unique(name)`; `btree(category, enabled)`; `btree(policy_status)`.

**Retention.** While active.

**DDL sketch.**

```sql
CREATE TABLE source (
    id              uuid PRIMARY KEY,
    name            text NOT NULL UNIQUE,
    category        text NOT NULL,
    access_method   text NOT NULL CHECK (access_method IN ('api','rss','html','browser')),
    base_url        text,
    policy_status   text NOT NULL DEFAULT 'unknown'
                      CHECK (policy_status IN ('approved','restricted','pending','unknown','prohibited')),
    rate_limit      jsonb NOT NULL DEFAULT '{}'::jsonb,
    retention       jsonb NOT NULL DEFAULT '{}'::jsonb,
    freshness       jsonb NOT NULL DEFAULT '{}'::jsonb,
    config          jsonb NOT NULL DEFAULT '{}'::jsonb,  -- allowed_uses, robots, connector, schedule, timeout_seconds
    parser_ref      text,
    credential_ref  text,                                 -- env key name, NEVER a secret
    enabled         boolean NOT NULL DEFAULT false,
    schema_version  integer NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX source_category_enabled_idx ON source (category, enabled);
CREATE INDEX source_policy_status_idx    ON source (policy_status);
```

---

### 2.2 crawl_job

**Purpose.** The durable job queue — **one** table implementing a status machine + DLQ. Workers claim with `SELECT ... FOR UPDATE SKIP LOCKED`. No external broker.

**Columns.**

| Column | Type | Notes |
|--------|------|-------|
| `id` | `uuid` | PK |
| `source_id` | `uuid` | FK → `source(id)` (`ON DELETE RESTRICT`) |
| `job_type` | `text` | `fetch`\|`parse`\|`enrich`\|`alert`\|`freshness` |
| `status` | `text` | `pending`\|`claimed`\|`running`\|`done`\|`failed`\|`dead` |
| `dedupe_key` | `text` | **unique** — deterministic per job; replays never duplicate |
| `priority` | `int` | |
| `scheduled_at` | `timestamptz` | when due |
| `claimed_at` | `timestamptz` | for visibility-timeout reclaim |
| `claimed_by` | `text` | worker id |
| `attempt` | `int` | |
| `max_attempts` | `int` | exceeded → `status=dead` (DLQ) |
| `last_error` | `text` | **sanitized before storage** ([§ Secret-redaction invariant](#secret-redaction-invariant-r8x)) |
| `payload` | `jsonb` | **sanitized before storage** (same helper) |
| `finished_at` | `timestamptz` | |
| `created_at` | `timestamptz` | |

**Claim semantics.** `SELECT ... FOR UPDATE SKIP LOCKED`. Jobs exceeding `max_attempts` move to `status=dead` (DLQ), inspectable via CLI (`atlas` job inspection). Claimed-but-stale jobs (`claimed_at` older than the visibility timeout) are reclaimed to `pending`.

**Indexes.** `btree(status, scheduled_at)`; `unique(dedupe_key)`; `btree(source_id)`.

**Retention.** 90 days (done/dead pruned) — **implemented (B16)** by `storage.retention.prune_crawl_jobs` / `atlas audit rotate` (terminal rows only, keyed on `coalesce(finished_at, created_at)`; window `CRAWL_JOB_RETENTION`, default 90d; never prunes live work). [ADR 0027](../decisions/0027-audit-rotation-and-crawl-job-retention.md).

> <a id="secret-redaction-invariant-r8x"></a>**Secret-redaction invariant.** Before persisting `crawl_job.last_error`, `crawl_job.payload`, or `audit_log.detail`, the writer **must** pass the value through one `sanitize_for_storage` helper in `packages/security/secrets`: redact known-secret patterns, strip URL **query** and **userinfo**, and strip `Authorization`/`Cookie` headers. Storing resolved credential values or credential-bearing URLs (e.g. FRED's `?api_key=...`) in any `jsonb`/`text` column is **forbidden**; backups (`pg_dump`) inherit this. The B15 secrets-in-logs regression test covers `last_error` and `audit_log.detail`.

**DDL sketch.**

```sql
CREATE TABLE crawl_job (
    id            uuid PRIMARY KEY,
    source_id     uuid NOT NULL REFERENCES source(id) ON DELETE RESTRICT,
    job_type      text NOT NULL CHECK (job_type IN ('fetch','parse','enrich','alert','freshness')),
    status        text NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','claimed','running','done','failed','dead')),
    dedupe_key    text NOT NULL UNIQUE,
    priority      integer NOT NULL DEFAULT 0,
    scheduled_at  timestamptz NOT NULL,
    claimed_at    timestamptz,
    claimed_by    text,
    attempt       integer NOT NULL DEFAULT 0,
    max_attempts  integer NOT NULL DEFAULT 5,
    last_error    text,            -- sanitize_for_storage() before write
    payload       jsonb,           -- sanitize_for_storage() before write
    finished_at   timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX crawl_job_status_sched_idx ON crawl_job (status, scheduled_at);
CREATE INDEX crawl_job_source_idx       ON crawl_job (source_id);
```

---

### 2.3 raw_document

**Purpose.** Immutable, content-addressed pointer to the gzipped raw payload on disk. The on-disk blob is the evidence of record; this row is its index. Mutable **only** via the audited purge path.

**Columns.**

| Column | Type | Notes |
|--------|------|-------|
| `id` | `uuid` | PK |
| `source_id` | `uuid` | FK → `source(id)` RESTRICT |
| `crawl_job_id` | `uuid` | FK → `crawl_job(id)` RESTRICT |
| `fetched_at` | `timestamptz` | |
| `content_hash` | `text` | **unique** — content address |
| `mime_type` | `text` | |
| `path` | `text` | disk path (content-addressed) |
| `size_bytes` | `bigint` | |
| `http_status` | `int` | |
| `etag` | `text` | conditional-request checkpoint |
| `last_modified` | `text` | conditional-request checkpoint |
| `origin_url` | `text` | final public fetch URL for provenance/reparse; nullable on legacy rows |
| `parser_ref` | `text` | local parser for secondary entry payloads; nullable on normal feed/index raw |
| `schema_version` | `int` | |

**Indexes.** `unique(content_hash)`; `btree(source_id, fetched_at)`.

**Retention.** Gzip on disk; prune low-value after 1–2y; deletable **only** via the purge path (records a retained `audit_log` entry). Re-parsing never mutates raw.

**DDL sketch.**

```sql
CREATE TABLE raw_document (
    id             uuid PRIMARY KEY,
    source_id      uuid NOT NULL REFERENCES source(id) ON DELETE RESTRICT,
    crawl_job_id   uuid REFERENCES crawl_job(id) ON DELETE RESTRICT,
    fetched_at     timestamptz NOT NULL,
    content_hash   text NOT NULL UNIQUE,
    mime_type      text,
    path           text NOT NULL,
    size_bytes     bigint,
    http_status    integer,
    etag           text,
    last_modified  text,
    origin_url     text,
    parser_ref     text,
    schema_version integer NOT NULL
);
CREATE INDEX raw_document_source_fetched_idx ON raw_document (source_id, fetched_at);
```

> **Migration 0013 / ADR 0039.** Secondary entry payloads such as SEC primary filings are preserved
> as their own raw objects. `origin_url` and `parser_ref` keep those objects self-describing while
> parse workers remain network-free. Existing rows require no backfill.

---

### 2.4 document

**Purpose.** The **current-state** canonical normalized record (unified article / RSS / feed item). One row per logical document.

> <a id="r2"></a>**R2 — versioning.** `document` holds **current state only**. Re-parsing **UPDATES the row in place** (last-write-wins). Old parsed versions are **NOT retained** but **ARE reproducible** by re-running the pinned `parser_version` against the immutable `raw_document`. `content_hash` (of the parsed body) detects when a re-parse changed output. Invariant: raw is immutable; normalized is **reproducible, not retained**; there is **no versioned parsed-document table**.

**Note.** `summary` is **NOT** on `document`; summaries live in the [`summary`](#25-summary) table.

**Columns.**

| Column | Type | Notes |
|--------|------|-------|
| `id` | `uuid` | PK |
| `source_id` | `uuid` | FK → `source(id)` RESTRICT |
| `raw_document_id` | `uuid` | FK → `raw_document(id)` RESTRICT — the raw it was parsed from |
| `canonical_url` | `text` | nullable; unique when present |
| `title` | `text` | |
| `body_text` | `text` | sanitized text (FTS source) |
| `language` | `text` | |
| `topics` | `text[]` | non-referential array |
| `entity_ids` | `text[]` | non-referential array (reconciled by purge) |
| `published_at` | `timestamptz` | UTC |
| `source_local_published_at` | `timestamptz` | source-local, kept separately |
| `ingested_at` | `timestamptz` | |
| `parsed_at` | `timestamptz` | |
| `content_hash` | `text` | hash of parsed body (re-parse change detector) |
| `cluster_id` | `uuid`/`text` | dedupe cluster |
| `parser_version` | `text` | |
| `schema_version` | `int` | |

**Indexes.**
- `unique(canonical_url) WHERE canonical_url IS NOT NULL` — **partial unique** so many null-URL docs coexist while real URLs dedupe.
- `GIN` tsvector over `body_text` (FTS).
- `GIN(topics)`; `GIN(entity_ids)`.
- `btree(published_at)`; `btree(source_id, published_at)`; `btree(cluster_id)`.

**Retention.** Keep (small, useful); deletable via purge.

**DDL sketch.**

```sql
CREATE TABLE document (
    id                          uuid PRIMARY KEY,
    source_id                   uuid NOT NULL REFERENCES source(id) ON DELETE RESTRICT,
    raw_document_id             uuid NOT NULL REFERENCES raw_document(id) ON DELETE RESTRICT,
    canonical_url               text,
    title                       text,
    body_text                   text,
    language                    text,
    topics                      text[] NOT NULL DEFAULT '{}',
    entity_ids                  text[] NOT NULL DEFAULT '{}',
    published_at                timestamptz,
    source_local_published_at   timestamptz,
    ingested_at                 timestamptz NOT NULL DEFAULT now(),
    parsed_at                   timestamptz,
    content_hash                text NOT NULL,
    cluster_id                  uuid,
    parser_version              text NOT NULL,
    schema_version              integer NOT NULL
);
-- partial unique on canonical_url
CREATE UNIQUE INDEX document_canonical_url_uidx
    ON document (canonical_url) WHERE canonical_url IS NOT NULL;
-- full-text search
CREATE INDEX document_fts_idx
    ON document USING gin (to_tsvector('simple', coalesce(body_text,'')));
CREATE INDEX document_topics_idx     ON document USING gin (topics);
CREATE INDEX document_entity_ids_idx ON document USING gin (entity_ids);
CREATE INDEX document_published_idx  ON document (published_at);
CREATE INDEX document_src_pub_idx    ON document (source_id, published_at);
CREATE INDEX document_cluster_idx    ON document (cluster_id);
```

> **Implemented (B5) — migration `0003`.** The plain btree indexes
> (`published_at`, `(source_id, published_at)`, `cluster_id`) are declared on the ORM model so
> Alembic autogenerate manages them. The three expression/partial indexes Alembic cannot
> round-trip — the **partial-unique** `uq_document_canonical_url` (`WHERE canonical_url IS NOT
> NULL`), the **FTS** `ix_document_fts` (`gin (to_tsvector('simple', coalesce(body_text,'')))`),
> and the **array GINs** `ix_document_topics` / `ix_document_entity_ids` — are created via raw
> SQL in the migration and excluded from the autogenerate drift-check by `_include_object` in
> `migrations/env.py`. The parse worker writes documents with a **last-write-wins** upsert keyed
> on `canonical_url` (R2): re-parsing refreshes parser-owned fields in place and leaves
> enrichment-owned columns (`topics`/`entity_ids`/`cluster_id`) untouched. Null-`canonical_url`
> docs dedupe best-effort on `(source_id, raw_document_id, content_hash)`.

> **Search query path (B6) — `atlascore.storage.search.search_documents` + `atlas search`.**
> The FTS query reuses `ix_document_fts` by matching its expression *exactly*
> (`to_tsvector('simple', coalesce(body_text,'')) @@ websearch_to_tsquery('simple', :q)`); the
> `'simple'` config is load-bearing and must stay in lockstep with the index (changing it requires
> a reindex). `websearch_to_tsquery` is used so arbitrary input never raises (supports quoted
> phrases / `or` / `-term`). Results rank by `ts_rank` (tie-broken `published_at DESC NULLS LAST`),
> carry a `ts_headline` snippet (matches in `«…»`) + provenance, and are capped (`MAX_LIMIT=100`).
> Optional `source`/`since`/`until` filters are bound parameters guarded by
> `CAST(:p AS …) IS NULL OR …`, so the SQL is static with no injection surface. Only sanitized
> `body_text` is searched/returned — never raw HTML (§13). An `EXPLAIN`-with-seqscan-off
> integration test asserts the index is used. This is the contract B7's MCP `search_documents`
> reuses ([ADR 0016](../decisions/0016-b6-fts-query-path-and-search-contract.md)).

---

### 2.5 summary

**Purpose.** Cached LLM summaries with full provenance. Powers `get_document_summary`. Covers **single**-doc and **multi**-doc summaries.

> <a id="r4"></a>**R4 — content-hash cache.** The **cache key** is `unique(source_hash, model_name, model_version, prompt_version)`. `source_hash` is a hash over the **ordered** member `content_hash`es. Staleness: any member's `content_hash` change changes `source_hash` → cache miss → recompute. **Never** re-summarize an identical `(content, model, prompt)` tuple. Dedupe-before-summarize.

**Columns.**

| Column | Type | Notes |
|--------|------|-------|
| `id` | `uuid` | PK |
| `scope` | `text` | `single`\|`multi` |
| `document_id` | `uuid` or `null` | FK → `document(id)` RESTRICT; `null` for `multi` |
| `member_doc_ids` | `text[]` | **ordered**; `single=[doc]`, `multi=[...]`; non-referential array |
| `source_hash` | `text` | hash over ordered member `content_hash`es |
| `model_name` | `text` | |
| `model_version` | `text` | |
| `prompt_version` | `text` | |
| `summary_text` | `text` | |
| `citations` | `jsonb` | internal doc IDs → claims |
| `token_cost` | `int` | |
| `created_at` | `timestamptz` | |
| `schema_version` | `int` | |

**Indexes.** `unique(source_hash, model_name, model_version, prompt_version)`; `btree(document_id)`; **`GIN(member_doc_ids)`** (required so purge can find multi-doc summaries by member — [R4 purge](#purge-cascade-r3)).

**Retention.** With parent docs / prune with raw.

**DDL sketch.**

```sql
CREATE TABLE summary (
    id              uuid PRIMARY KEY,
    scope           text NOT NULL CHECK (scope IN ('single','multi')),
    document_id     uuid REFERENCES document(id) ON DELETE RESTRICT,  -- null for multi
    member_doc_ids  text[] NOT NULL,                                  -- ordered
    source_hash     text NOT NULL,
    model_name      text NOT NULL,
    model_version   text NOT NULL,
    prompt_version  text NOT NULL,
    summary_text    text NOT NULL,
    citations       jsonb NOT NULL DEFAULT '{}'::jsonb,
    token_cost      integer,
    created_at      timestamptz NOT NULL DEFAULT now(),
    schema_version  integer NOT NULL,
    CONSTRAINT summary_cache_key UNIQUE (source_hash, model_name, model_version, prompt_version)
);
CREATE INDEX summary_document_idx    ON summary (document_id);
CREATE INDEX summary_member_ids_idx  ON summary USING gin (member_doc_ids);  -- purge must scan this
```

> **Implemented (B12) — migration `0008`** ([ADR 0022](../decisions/0022-b12-summaries-pipeline-and-content-hash-cache.md)). Raw SQL (the `GIN(member_doc_ids)` array index doesn't round-trip through autogenerate); excluded from the drift-check by owning table. `storage.summaries` computes `source_hash` over the ordered member `content_hash`es, looks up / inserts on the R4 cache key (idempotent `ON CONFLICT DO NOTHING`), and the `enrichment.summaries` pipeline computes-on-miss only when a `SummaryBackend` is configured (`LLM_BACKEND`). MVP's `local` backend is the deterministic `ExtractiveSummarizer` (cited lead-sentence extraction); hosted LLMs are pluggable behind the `api` extra. `citations` map each claim to the **internal** `document.id` (never an external URL).

---

### 2.6 embedding

**Purpose.** Vector embeddings of document chunks for ANN retrieval (`pgvector`).

> <a id="r8"></a>**R8 — fixed dimension.** `pgvector`'s `vector(N)` requires a **fixed dimension per ANN index**. Therefore **exactly one active embedding model + dim per deployment**, set by config (`EMBEDDING_BACKEND`/`EMBEDDING_MODEL`/`EMBEDDING_DIM`). Switching backends/dims is a **documented, bounded, resumable backfill + reindex** (see [runbooks](../runbooks/README.md)), never an online change. `dim` + `model_version` are stored for provenance and mismatch detection.

> <a id="r13"></a>**R13 — idempotency.** `unique(document_id, chunk_index, model_name, model_version)`. Re-running enrichment for an unchanged `source_hash` is a **no-op (skip)**, mirroring the `summary` cache rule. This preserves the "idempotent, safe to replay" invariant required by build block B11.

**Columns.**

| Column | Type | Notes |
|--------|------|-------|
| `id` | `uuid` | PK |
| `document_id` | `uuid` | FK → `document(id)` RESTRICT |
| `chunk_index` | `int` | |
| `model_name` | `text` | |
| `model_version` | `text` | |
| `dim` | `int` | must match the deployment's `vector(N)` |
| `vector` | `vector(N)` | N fixed per deployment |
| `source_hash` | `text` | re-embed skip key |
| `created_at` | `timestamptz` | |

**Indexes.** `pgvector` ANN index (**HNSW preferred**) on `vector`; `btree(document_id)`; `unique(document_id, chunk_index, model_name, model_version)`.

**Retention.** With parent doc.

**DDL sketch.** (`N` is the deployment's `EMBEDDING_DIM`; shown as `384` for the default local model.)

```sql
CREATE TABLE embedding (
    id            uuid PRIMARY KEY,
    document_id   uuid NOT NULL REFERENCES document(id) ON DELETE RESTRICT,
    chunk_index   integer NOT NULL,
    model_name    text NOT NULL,
    model_version text NOT NULL,
    dim           integer NOT NULL,
    vector        vector(384) NOT NULL,     -- N == EMBEDDING_DIM, fixed per deployment (R8)
    source_hash   text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT embedding_chunk_uniq UNIQUE (document_id, chunk_index, model_name, model_version)
);
CREATE INDEX embedding_document_idx ON embedding (document_id);
CREATE INDEX embedding_ann_idx ON embedding USING hnsw (vector vector_cosine_ops);
```

> **Implemented (B11) — migration `0007`** ([ADR 0021](../decisions/0021-b11-enrichment-entity-mention-embedding.md)). Created via **raw SQL** (the `vector(N)` column + HNSW index don't round-trip through autogenerate); `N` is pinned at **384** (`EMBEDDING_DIM`). The whole `embedding` table is excluded from the drift-check by owning table in `migrations/env.py`. `storage.embeddings.upsert_embeddings` chunks the body, embeds via the configured `EmbeddingBackend`, and is a **no-op skip** when the stored `source_hash` (= `document.content_hash`) is unchanged (R13). MVP's `local` backend is the deterministic `HashingEmbeddingBackend`; sentence-transformers/cloud are pluggable upgrades (same dim → no reindex; a different dim is the R8 runbook).

---

### 2.7 time_series_point

**Purpose.** Unified macro observations + market bars. `kind` discriminates: macro uses `value`; market bars use OHLCV. Accepted sparse-column tradeoff for one lean table.

> <a id="r1"></a>**R1 — idempotent upsert.** `UNIQUE NULLS NOT DISTINCT (provider, series_id, ts, vintage)`. This treats two `NULL` vintages as equal, guaranteeing idempotent upserts even when `vintage` is `null` (the common case). **`NULLS NOT DISTINCT` requires PostgreSQL 15+** (introduced in PG15, *not* PG16). Pre-15 fallback: a generated `vintage_key = coalesce(vintage,'epoch')` column inside the unique index.

> <a id="partition-pk"></a>**Composite PK on partitioned tables.** The table is **RANGE-partitioned on `ts`**. PostgreSQL **rejects** a primary-key or unique constraint on a partitioned table unless it **includes all partitioning columns** (error: *"unique constraint on partitioned table must include all partitioning columns"*). Therefore the surrogate PK is **composite**: `PRIMARY KEY (id, ts)`, **not** `PRIMARY KEY (id)`. The `UNIQUE` in R1 already includes `ts`, so it is valid. **This rule applies to any future partitioned table.**

**Columns.**

| Column | Type | Notes |
|--------|------|-------|
| `id` | `uuid` | part of composite PK |
| `provider` | `text` | e.g. `fred` |
| `series_id` | `text` | |
| `ts` | `timestamptz` | partition key; part of composite PK |
| `kind` | `text` | `observation`\|`bar` |
| `value` | `numeric` | scalar series (macro) |
| `open` | `numeric` | OHLCV; nullable; only `kind=bar` |
| `high` | `numeric` | nullable |
| `low` | `numeric` | nullable |
| `close` | `numeric` | nullable |
| `volume` | `numeric` | nullable |
| `unit` | `text` | |
| `vintage` | `timestamptz` | nullable; data-vintage / as-of for revisable macro series |
| `schema_version` | `int` | |

**Partitioning.** RANGE on `ts` (monthly/yearly), optional LIST sub-partition by `provider`; Timescale hypertable optional.

**Indexes.** the R1 unique; `btree(provider, series_id, ts)`.

**Retention.** Keep; retain vintages.

**DDL sketch.**

```sql
CREATE TABLE time_series_point (
    id             uuid NOT NULL,
    provider       text NOT NULL,
    series_id      text NOT NULL,
    ts             timestamptz NOT NULL,
    kind           text NOT NULL CHECK (kind IN ('observation','bar')),
    value          numeric,
    open           numeric,
    high           numeric,
    low            numeric,
    close          numeric,
    volume         numeric,
    unit           text,
    vintage        timestamptz,
    schema_version integer NOT NULL,
    -- composite PK MUST include the partition key (ts):
    PRIMARY KEY (id, ts),
    -- idempotent upsert; NULLS NOT DISTINCT requires PG15+:
    CONSTRAINT tsp_natural_key UNIQUE NULLS NOT DISTINCT (provider, series_id, ts, vintage)
) PARTITION BY RANGE (ts);

CREATE INDEX tsp_provider_series_ts_idx ON time_series_point (provider, series_id, ts);

-- Pre-PG15 fallback (only if pinned <15): drop the NULLS NOT DISTINCT unique and use
--   vintage_key text GENERATED ALWAYS AS (coalesce((vintage AT TIME ZONE 'UTC')::text,'epoch')) STORED
--   -- note: AT TIME ZONE 'UTC' yields a plain timestamp whose ::text cast is IMMUTABLE,
--   --       which a STORED generated column requires (a bare timestamptz::text is only STABLE).
--   UNIQUE (provider, series_id, ts, vintage_key)
```

> **Implemented (B9) — migration `0006`, the first partitioned table** ([ADR 0019](../decisions/0019-b9-time-series-fred-connector-and-macro-series.md)). Created via **raw SQL** (autogenerate cannot emit `PARTITION BY`): parent `PARTITION BY RANGE (ts)`, composite PK `(id, ts)`, `kind` CHECK, and `CONSTRAINT time_series_point_natural_key UNIQUE NULLS NOT DISTINCT (provider, series_id, ts, vintage)`. A **DEFAULT partition** (`time_series_point_default`) catches every `ts`. **Yearly partitions — implemented (B17, migration `0010`)** ([ADR 0028](../decisions/0028-b17-time-series-yearly-partitions.md)): `storage.partitions.ensure_yearly_partitions` / `atlas tsp partitions ensure` carve bounded `time_series_point_<year>` children via the detach/move/attach with-rows dance (children inherit the composite PK + natural key; DEFAULT stays as the catch-all), and `worker.partitions.run_partition_maintenance` (advisory-locked; the B19 scheduler's monthly `JobType.PARTITION` job) keeps the next year ahead. All `time_series_point*` objects (parent, DEFAULT, and yearly children) are **excluded from the autogenerate drift-check** by a name-prefix rule in `migrations/env.py` (the ORM model maps columns for querying only). `storage.timeseries.upsert_observations` upserts idempotently on the natural key (`ON CONFLICT … DO UPDATE`) and routes rows to the right child; `read_macro_series` powers `get_macro_series` transparently across partitions. **The composite-PK rule applies to any future partitioned table.**

---

### 2.8 entity

**Purpose.** Canonical entities (companies/assets/topics/people) with aliases, external identifiers, and merge history.

**Columns.**

| Column | Type | Notes |
|--------|------|-------|
| `id` | `uuid` | PK |
| `entity_type` | `text` | |
| `canonical_name` | `text` | trigram-indexed |
| `aliases` | `text[]` | |
| `identifiers` | `jsonb` | CIK / LEI / ticker / wikidata |
| `confidence` | `numeric` | |
| `merged_into` | `uuid` or `null` | merge history (self-reference) |
| `schema_version` | `int` | |
| `created_at` | `timestamptz` | |

**Indexes.** **trigram** GIN on `canonical_name` using `gin_trgm_ops` (**requires `pg_trgm`**); `GIN(aliases)`; `GIN(identifiers)`.

**Retention.** Keep, with merge history.

**DDL sketch.**

```sql
CREATE TABLE entity (
    id             uuid PRIMARY KEY,
    entity_type    text NOT NULL,
    canonical_name text NOT NULL,
    aliases        text[] NOT NULL DEFAULT '{}',
    identifiers    jsonb NOT NULL DEFAULT '{}'::jsonb,
    confidence     numeric,
    merged_into    uuid REFERENCES entity(id) ON DELETE RESTRICT,
    schema_version integer NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now()
);
-- pg_trgm must be installed (early migration):
CREATE INDEX entity_name_trgm_idx   ON entity USING gin (canonical_name gin_trgm_ops);
CREATE INDEX entity_aliases_idx     ON entity USING gin (aliases);
CREATE INDEX entity_identifiers_idx ON entity USING gin (identifiers);
```

> **Implemented (B11) — migration `0007`** ([ADR 0021](../decisions/0021-b11-enrichment-entity-mention-embedding.md)). Raw SQL (trigram + array GINs); excluded from the drift-check by owning table. `storage.entities.find_or_create_entity` resolves a NER candidate by identifier (jsonb containment) then exact normalized name/alias, else creates; `resolve_entity` (the `get_entity_mentions` read side) adds **alias-aware trigram fuzzy** name matching and follows `merged_into` to the surviving id.

---

### 2.9 mention

**Purpose.** A mention of an `entity` in a `document`, with confidence and optional sentiment. Powers `get_entity_mentions`.

**Columns.**

| Column | Type | Notes |
|--------|------|-------|
| `id` | `uuid` | PK |
| `entity_id` | `uuid` | FK → `entity(id)` RESTRICT |
| `document_id` | `uuid` | FK → `document(id)` RESTRICT |
| `mentioned_at` | `timestamptz` | |
| `confidence` | `numeric` | |
| `sentiment` | `numeric` or `null` | nullable |
| `context_snippet` | `text` | |
| `model_name` | `text` | enrichment provenance |
| `model_version` | `text` | enrichment provenance |

**Indexes.** `btree(entity_id, mentioned_at)`; `btree(document_id)`.

**Retention.** With parent doc.

**DDL sketch.**

```sql
CREATE TABLE mention (
    id              uuid PRIMARY KEY,
    entity_id       uuid NOT NULL REFERENCES entity(id) ON DELETE RESTRICT,
    document_id     uuid NOT NULL REFERENCES document(id) ON DELETE RESTRICT,
    mentioned_at    timestamptz,
    confidence      numeric,
    sentiment       numeric,
    context_snippet text,
    model_name      text NOT NULL,
    model_version   text NOT NULL
);
CREATE INDEX mention_entity_idx   ON mention (entity_id, mentioned_at);
CREATE INDEX mention_document_idx ON mention (document_id);
```

> **Implemented (B11) — migration `0007`** ([ADR 0021](../decisions/0021-b11-enrichment-entity-mention-embedding.md)). Raw SQL; excluded from the drift-check by owning table. Adds a **unique `(entity_id, document_id, model_name, model_version)`** index so re-enriching a document upserts mentions idempotently (the pipeline clears + re-creates this document's mentions for the current NER model+version). `context_snippet` is taken from the already-sanitized `document.body_text` (evidence, not instruction); `sentiment` stays `null` in MVP (never fabricated). `storage.entities.list_mentions` joins document/source for `get_entity_mentions` provenance.

---

### 2.10 alert

**Purpose.** Rule-based alerts with dedupe and evidence doc IDs. Powers `get_alerts`.

**Columns.**

| Column | Type | Notes |
|--------|------|-------|
| `id` | `uuid` | PK |
| `alert_type` | `text` | `macro_release`\|`cve_watch`\|`sec_filing`\|... |
| `rule_id` | `text` | |
| `severity` | `text` | |
| `status` | `text` | `open`\|`acknowledged`\|`resolved`\|`purged` |
| `title` | `text` | |
| `dedupe_key` | `text` | **unique** |
| `evidence_doc_ids` | `text[]` | non-referential array (reconciled by purge) |
| `detail` | `jsonb` | |
| `delivered_at` | `timestamptz` | |
| `delivery_status` | `text` | |
| `created_at` | `timestamptz` | |
| `schema_version` | `int` | |

**Lifecycle.** `open → acknowledged → resolved`; `purged` when evidence is erased (all evidence removed by purge → `status=purged`).

**Indexes.** `unique(dedupe_key)`; `btree(status, severity, created_at)`.

**Retention.** 6–12 months.

**DDL sketch.**

```sql
CREATE TABLE alert (
    id                uuid PRIMARY KEY,
    alert_type        text NOT NULL,
    rule_id           text NOT NULL,
    severity          text NOT NULL,
    status            text NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open','acknowledged','resolved','purged')),
    title             text NOT NULL,
    dedupe_key        text NOT NULL UNIQUE,
    evidence_doc_ids  text[] NOT NULL DEFAULT '{}',
    detail            jsonb NOT NULL DEFAULT '{}'::jsonb,
    delivered_at      timestamptz,
    delivery_status   text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    schema_version    integer NOT NULL
);
CREATE INDEX alert_status_idx ON alert (status, severity, created_at);
```

> **Implemented (B13) — migration `0009`** ([ADR 0023](../decisions/0023-b13-rule-based-alerts-delivery-and-get-alerts.md)). A **normal** table (`op.create_table`, autogenerate/drift-checked — unlike the raw-SQL enrichment/summary tables). `storage.alerts.upsert_alert` inserts idempotently on `dedupe_key` (`ON CONFLICT DO NOTHING`) with `detail` sanitized via `sanitize_for_storage`; `list_alerts` powers `get_alerts` (status/type/`min_severity`/`since` filters + batched evidence→document/source resolution). Four rules (`alerts.rules`): `sec_filing`/`cve_watch` (by source category, cite the document), `macro_release` (latest FRED observation per series), `freshness` (stale/error `source_health`). The alert worker (`atlas alerts run`) evaluates + delivers over the configured channel (which also backs `atlas digest`). MVP fires on every matching item (no per-issuer/CVSS watchlist yet).

---

### 2.11 audit_log

**Purpose.** Append-only audit trail of control-plane and tool actions.

> <a id="r14"></a>**R14 — retention class.** A `retention_class` column distinguishes erasure entries from routine ones: `purge` for erasure/purge entries (**retained — never rotated away**, so erasure stays provable) and `routine` otherwise. The rotation job deletes **only** `retention_class='routine'` beyond the window. This replaces fragile action-string matching.

**Columns.**

| Column | Type | Notes |
|--------|------|-------|
| `id` | `uuid` | PK |
| `ts` | `timestamptz` | |
| `action` | `text` | |
| `actor` | `text` | `system`\|`cli`\|`mcp` |
| `tool_name` | `text` or `null` | MCP tool name when actor=mcp |
| `source_id` | `uuid` or `null` | |
| `target_ref` | `text` or `null` | doc/entity id |
| `retention_class` | `text` | `purge` (retained) \| `routine` (rotates) |
| `detail` | `jsonb` | **sanitized before storage** ([secret-redaction invariant](#secret-redaction-invariant-r8x)) — records action + hashes for purge, **never** personal data |

**Indexes.** `btree(ts)`; `btree(action)`.

**Retention.** Routine ~few months (rotated); `retention_class='purge'` entries retained indefinitely. **Implemented (B16):** `storage.retention.rotate_audit_log` / `atlas audit rotate` deletes only `retention_class='routine'` rows beyond `AUDIT_ROUTINE_RETENTION` (default 180d) — filtered on `retention_class`, never action strings (R14) — and writes one retained `purge` bookkeeping entry. [ADR 0027](../decisions/0027-audit-rotation-and-crawl-job-retention.md).

**DDL sketch.**

```sql
CREATE TABLE audit_log (
    id              uuid PRIMARY KEY,
    ts              timestamptz NOT NULL DEFAULT now(),
    action          text NOT NULL,
    actor           text NOT NULL CHECK (actor IN ('system','cli','mcp')),
    tool_name       text,
    source_id       uuid,
    target_ref      text,
    retention_class text NOT NULL DEFAULT 'routine'
                      CHECK (retention_class IN ('purge','routine')),
    detail          jsonb NOT NULL DEFAULT '{}'::jsonb   -- sanitize_for_storage() before write
);
CREATE INDEX audit_log_ts_idx     ON audit_log (ts);
CREATE INDEX audit_log_action_idx ON audit_log (action);
```

> **Implemented (B7) — migration `0004`** ([ADR 0017](../decisions/0017-b7-mcp-server-search-documents-and-audit-log.md)). ORM `AuditLog` + `Actor`/`RetentionClass` enums (`schemas/audit_log.py`); CHECK constraints on `actor` and `retention_class`; `ts` defaults to DB `now()`; `detail` defaults to `'{}'::jsonb`. **No foreign keys** by design — the append-only log must outlive purge/deletion of the rows it references (`source_id`, and `target_ref`, which is `text` precisely so a purged doc's hash-only entry still stands). All writes go through `storage.audit.write_audit_log`, which runs `detail` through `sanitize_for_storage` before it reaches the column (§14). The B7 MCP server writes one `routine` row per tool call; the purge path (B14) writes retained `purge` rows.

---

### 2.12 source_health

**Purpose.** Per-source freshness/health snapshot. Computed by the `job_type=freshness` job; powers `get_source_health` and the daily digest. Compares `freshness_lag` against `source.freshness` thresholds.

**Columns.**

| Column | Type | Notes |
|--------|------|-------|
| `source_id` | `uuid` | **PK**, FK → `source(id)` RESTRICT |
| `last_attempt_at` | `timestamptz` | |
| `last_success_at` | `timestamptz` | |
| `last_item_published_at` | `timestamptz` | |
| `freshness_lag` | `interval` | `now - coalesce(last_item_published_at, last_success_at)` |
| `status` | `text` | `ok`\|`warn`\|`stale`\|`error`\|`cooling_down` |
| `consecutive_failures` | `int` | feeds circuit-breaker (`cooling_down`) |
| `success_rate_24h` | `numeric` | |
| `parse_quality` | `numeric` | |
| `duplicate_rate` | `numeric` | |
| `item_count_recent` | `int` | |
| `updated_at` | `timestamptz` | |

**Computed by.** The freshness/health job on a schedule; `freshness_lag` vs `source.freshness` thresholds (`warn_after` → `warn`; `error_after` → `stale`/`error`, fires a freshness alert).

**Indexes.** `btree(status)`.

**Retention.** Current snapshot only (no history table in MVP).

**DDL sketch.**

```sql
CREATE TABLE source_health (
    source_id               uuid PRIMARY KEY REFERENCES source(id) ON DELETE RESTRICT,
    last_attempt_at         timestamptz,
    last_success_at         timestamptz,
    last_item_published_at  timestamptz,
    freshness_lag           interval,
    status                  text NOT NULL DEFAULT 'ok'
                              CHECK (status IN ('ok','warn','stale','error','cooling_down')),
    consecutive_failures    integer NOT NULL DEFAULT 0,
    success_rate_24h        numeric,
    parse_quality           numeric,
    duplicate_rate          numeric,
    item_count_recent       integer,
    updated_at              timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX source_health_status_idx ON source_health (status);
```

> **Implemented (B8) — migration `0005`** ([ADR 0018](../decisions/0018-b8-source-health-freshness-and-digest.md)). ORM `SourceHealth` + `HealthStatus` enum; FK `ON DELETE RESTRICT`; `freshness_lag` is a Postgres `interval`; `status` CHECK-bounded; `btree(status)`. The freshness computation lives in `worker/freshness.py`: `refresh_source_health` computes (read-only over `crawl_job` + `document`) and idempotently upserts each source's snapshot. Status is derived by the pure `classify_status` (precedence `cooling_down` > `stale` > `warn` > `error` > `ok`; `COOLING_THRESHOLD=3`). `atlas health` / `atlas digest` drive the computation then read it (the B19 scheduler now also runs it inline); `get_source_health` (MCP) and `storage.source_health.list_source_health` read a **secret-free** view (name + slug only). `duplicate_rate` is null in MVP.

### 2.13 llm_spend (operational, B21)

**Purpose.** The LLM cost ledger backing the hard monthly spend cap (§20). One row per paid hosted-LLM **or cloud-embedding** call. Not one of the 12 canonical data tables — a control-plane operational table (like the queue), so it sits outside the purge cascade.

**Columns.** `id` uuid PK, `ts` timestamptz default now(), `provider` text, `model` text, `request_kind` text (`summary` | `embedding`), `input_tokens` int, `output_tokens` int, `est_cost_eur` numeric(12,6), `period_ym` text ('YYYY-MM'), `summary_id` uuid **(non-FK** — decoupled from the raw-SQL `summary` table, purge-simple; null for `embedding` rows), `created_at`, `schema_version`. **Index** `btree(period_ym)` for month-to-date sums.

> **Implemented (B21) — migration `0012`** ([ADR 0032](../decisions/0032-b21-real-backends-spend-cap-spacy-reindex.md)). Normal autogenerate-managed table. `storage.llm_spend` exposes `estimate_cost_eur` (conservative per-provider price table), `month_to_date_spend(period_ym)`, and `record_spend(...)`. The summary pipeline sums month-to-date spend before any paid (`SummaryBackend.is_paid`) compute and **degrades to cache-only** at/over `LLM_MONTHLY_SPEND_CAP_EUR`; a successful paid compute records one ledger row in the summary's transaction.
>
> **Cloud embeddings share the same ledger + cap (`request_kind='embedding'`)** — `storage.embeddings.upsert_embeddings` checks month-to-date spend before a paid (`is_paid`) cloud embed and **skips** it at/over the cap (graceful degradation: existing vectors untouched, FTS still works), recording one ledger row on a successful embed (summaries + embeddings draw on one shared monthly budget). This closes the B21 gap where only summaries were metered ([ADR 0034](../decisions/0034-soak-readiness-hardening.md)).

---

## 5. Purge Cascade (R3)

> Immutability of `raw_document` has **exactly one** documented exception: the **audited `atlas purge` path**. Purge is **idempotent**. The full procedure lives in [`docs/runbooks/README.md`](../runbooks/README.md); the data-model contract is summarized here.

<a id="purge-cascade-r3"></a>**Given a `doc_id`:**

1. Delete `embedding` rows where `document_id = doc_id`.
2. Delete `mention` rows where `document_id = doc_id`.
3. Delete `summary` rows where **`document_id = doc_id` OR `doc_id = ANY(member_doc_ids)`** — this **must scan `summary.member_doc_ids[]`** (the `GIN(member_doc_ids)` index, [R4](#25-summary)). Multi-scope summaries set `document_id=null` and link only via `member_doc_ids[]`; a `document_id`-only delete would leave purged personal data alive in `summary_text`/`citations`. Affected multi-doc summaries are **recomputed or hard-deleted**.
4. Remove the doc from FTS (the `document` row deletion drops its tsvector index entry).
5. Delete the `document` row.
6. Delete the `raw_document` **file on disk** + its row.
7. Scan `alert.evidence_doc_ids[]` and remove the id; an alert that loses **all** evidence → `status='purged'`.
8. Write a **retained** `audit_log` entry (`retention_class='purge'`) recording **action + hashes** — **never** the personal data itself.

**Given an `entity_id`:** delete the `entity` row + its `mention` rows; **documents survive**. Write a retained `audit_log` entry.

**FK ON DELETE choices (R17).** All FKs use **`ON DELETE NO ACTION`/`RESTRICT`** so purge **ordering is application-driven and DB-enforced** (RESTRICT prevents an accidental `ON DELETE CASCADE` from over-deleting on entity purge). Array-of-id columns (`document.topics`, `document.entity_ids`, `alert.evidence_doc_ids`, `summary.member_doc_ids`) are **intentionally non-referential** and reconciled only by the purge job.

**Regression test.** A purge regression test covers a **multi-doc summary** (purging one member doc removes/recomputes the multi summary) — see `tests/integration/`.

---

## 6. Compliance & Backend Notes (cross-reference)

- **Cloud backend PII egress.** When `EMBEDDING_BACKEND`/`LLM_BACKEND` is a **cloud** provider, document text — which (per [`docs/compliance/README.md`](../compliance/README.md)) inherently contains personal data (names, quotes) — **egresses to a third-party (and likely third-country) processor**. This is **not** automatically consistent with a pure internal-only/household-exemption posture. Posture: prefer **local backends for PII-bearing docs**, or document the processor reality (no-training / zero-retention / DPA settings). See [`docs/compliance/README.md`](../compliance/README.md) and [`docs/security/README.md`](../security/README.md).
- **Robots.** No HTML/browser source ships in the MVP, so no runtime robots gate is built in B1–B15; `source.config->>'robots'` is **policy-input/advisory** until an HTML source is approved (at which point a runtime robots check is added in that connector's block). See [`docs/compliance/README.md`](../compliance/README.md).

---

## 7. Source Registry YAML (canonical shapes)

<a id="source-registry-yaml"></a>The git-tracked YAML is the source of truth and mirrors into `source` (the `config jsonb` column holds `allowed_uses`/`robots`/`connector`/`schedule`/`timeout_seconds`, per [R9](#r9-source-table-round-trips-the-yaml)). **Structured `retention`/`freshness` jsonb is canonical; the old scalar `retention_ref` string is removed everywhere (R6).**

```yaml
id: sec_edgar_companyfacts
name: SEC EDGAR Company Facts
category: finance_filings
access_method: api
base_url: https://data.sec.gov
enabled: true
policy:
  status: approved
  robots: respect
  allowed_uses: [internal_search, internal_summary, agent_retrieval]
rate_limit: { max_requests: 10, window_seconds: 1 }
freshness: { target: "24h", warn_after: "36h", error_after: "72h" }
retention: { raw: "2y", normalized: "permanent" }
credentials: { ref: null }       # names an env key resolved at runtime; NEVER a secret value
fetch:
  connector: atlascore.connectors.sec.companyfacts:SecCompanyFactsConnector
  schedule: "*/15 * * * *"
  timeout_seconds: 30
```

- **Retention grammar:** `Nd | Nw | Nm | Ny`, or `"permanent"`. Stored in `source.retention` jsonb (`{raw, normalized, audit?}`).
- **Freshness:** `{target, warn_after, error_after}` stored in `source.freshness` jsonb; `freshness_lag = now - coalesce(last_item_published_at, last_success_at)`.
- **Default-deny:** missing/`unknown`/`pending`/`prohibited` ⇒ disabled; only `approved` (or `restricted` with encoded constraints) is fetched.

---

## 8. Consolidated JSON Example

One end-to-end example showing how a `source`, a parsed `document`, its cached `summary`, a `time_series_point`, and a derived `alert` relate. (Illustrative values; IDs are UUIDv7-style; all times UTC.)

```json
{
  "source": {
    "id": "018f9a10-0000-7000-8000-0000000000a1",
    "name": "SEC EDGAR Company Facts",
    "category": "finance_filings",
    "access_method": "api",
    "base_url": "https://data.sec.gov",
    "policy_status": "approved",
    "rate_limit": { "max_requests": 10, "window_seconds": 1 },
    "retention": { "raw": "2y", "normalized": "permanent" },
    "freshness": { "target": "24h", "warn_after": "36h", "error_after": "72h" },
    "config": {
      "allowed_uses": ["internal_search", "internal_summary", "agent_retrieval"],
      "robots": "respect",
      "connector": "atlascore.connectors.sec.companyfacts:SecCompanyFactsConnector",
      "schedule": "*/15 * * * *",
      "timeout_seconds": 30
    },
    "parser_ref": "atlascore.parsers.filing:FilingParser",
    "credential_ref": null,
    "enabled": true,
    "schema_version": 1,
    "created_at": "2026-06-03T00:00:00Z",
    "updated_at": "2026-06-03T00:00:00Z"
  },

  "document": {
    "id": "018f9a10-0001-7000-8000-0000000000b2",
    "source_id": "018f9a10-0000-7000-8000-0000000000a1",
    "raw_document_id": "018f9a10-0002-7000-8000-0000000000c3",
    "canonical_url": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0000320193",
    "title": "Apple Inc. — Company Facts update",
    "body_text": "Apple Inc. reported revenue of ... (sanitized parsed text)",
    "language": "en",
    "topics": ["earnings", "technology"],
    "entity_ids": ["018f9a10-0006-7000-8000-0000000000g7"],
    "published_at": "2026-06-02T20:15:00Z",
    "source_local_published_at": "2026-06-02T16:15:00-04:00",
    "ingested_at": "2026-06-03T00:05:00Z",
    "parsed_at": "2026-06-03T00:05:12Z",
    "content_hash": "sha256:9f1c...parsedbody",
    "cluster_id": "018f9a10-0007-7000-8000-0000000000h8",
    "parser_version": "filing@1.2.0",
    "schema_version": 1
  },

  "summary": {
    "id": "018f9a10-0003-7000-8000-0000000000d4",
    "scope": "single",
    "document_id": "018f9a10-0001-7000-8000-0000000000b2",
    "member_doc_ids": ["018f9a10-0001-7000-8000-0000000000b2"],
    "source_hash": "sha256:over-ordered-member-content-hashes",
    "model_name": "gpt-style-or-local",
    "model_version": "2026-05",
    "prompt_version": "summary-v3",
    "summary_text": "Apple's latest company facts show ... (cited).",
    "citations": { "018f9a10-0001-7000-8000-0000000000b2": ["revenue figure", "filing date"] },
    "token_cost": 512,
    "created_at": "2026-06-03T00:06:00Z",
    "schema_version": 1
  },

  "time_series_point": {
    "id": "018f9a10-0004-7000-8000-0000000000e5",
    "provider": "fred",
    "series_id": "CPIAUCSL",
    "ts": "2026-05-01T00:00:00Z",
    "kind": "observation",
    "value": 312.456,
    "open": null,
    "high": null,
    "low": null,
    "close": null,
    "volume": null,
    "unit": "index_1982_1984=100",
    "vintage": null,
    "schema_version": 1
  },

  "alert": {
    "id": "018f9a10-0005-7000-8000-0000000000f6",
    "alert_type": "sec_filing",
    "rule_id": "edgar_companyfacts_update",
    "severity": "info",
    "status": "open",
    "title": "New SEC EDGAR company-facts update for Apple Inc.",
    "dedupe_key": "sec_filing:0000320193:2026-06-02T20:15:00Z",
    "evidence_doc_ids": ["018f9a10-0001-7000-8000-0000000000b2"],
    "detail": { "cik": "0000320193", "form": "company_facts" },
    "delivered_at": "2026-06-03T00:07:00Z",
    "delivery_status": "sent",
    "created_at": "2026-06-03T00:06:30Z",
    "schema_version": 1
  }
}
```

---

## 9. Related Documents

- [`docs/architecture/README.md`](../architecture/README.md) — canonical flow, concurrency & multi-worker model.
- [`docs/connectors/README.md`](../connectors/README.md) — connector lifecycle, source YAML.
- [`docs/security/README.md`](../security/README.md) — SSRF/URL-safety, sanitization, `sanitize_for_storage`.
- [`docs/compliance/README.md`](../compliance/README.md) — internal-only posture, PII, robots, cloud-egress.
- [`docs/mcp/README.md`](../mcp/README.md) — the 7-tool MCP surface.
- [`docs/runbooks/README.md`](../runbooks/README.md) — purge procedure (R3), embedding backfill+reindex (R8), backups.
- [`docs/decisions/`](../decisions/) — ADRs (Context / Decision / Consequences), dated 2026-06-03.
