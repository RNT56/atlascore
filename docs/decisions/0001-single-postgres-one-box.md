# 0001. Single PostgreSQL instance on one box

- **Status:** Accepted
- **Date:** 2026-06-03
- **Deciders:** Operator (single-operator project)
- **Spec reference:** AtlasCore Canonical Decisions Spec v1 (2026-06-03), sections 0, 1, 2, 5, 11

## Context

AtlasCore is a governed, single-operator data fabric: one operator, internal-only
(never redistribute data), RSS + official/free APIs only, runs unattended on one
box. The system must do relational storage, full-text search, vector similarity,
a durable job queue, time-series storage, and an append-only audit trail.

A conventional "modern data stack" would reach for separate services for each
concern: a message broker (Kafka/RabbitMQ/Redis) for the queue, a search cluster
(OpenSearch/Elasticsearch) for FTS, a vector database (Qdrant/Pinecone) for
embeddings, an OLAP store (ClickHouse) for time-series, and an orchestrator
(Airflow/Kubernetes) to tie them together. For a single operator running
unattended on one VM with no powerful hardware, every additional service is an
additional thing to deploy, secure, back up, monitor, upgrade, and debug at 3am.
Operational surface area — not feature count — is the dominant risk to a project
that must run unattended for weeks.

PostgreSQL 16 alone can serve every one of these needs adequately at
single-operator scale: relational tables, `tsvector` full-text search, `pgvector`
for embeddings, `SELECT … FOR UPDATE SKIP LOCKED` for the job queue, RANGE-
partitioned tables (optionally a Timescale hypertable) for time-series, and an
append-only table for audit. Raw payloads do not belong in a relational engine
and are far cheaper on disk.

## Decision

AtlasCore runs on **exactly one PostgreSQL 16 instance on one box**, plus a
local-disk raw store. No external broker, search cluster, vector database, OLAP
store, or orchestration cluster is introduced in the MVP.

PostgreSQL is the baseline because it covers the full need set with mature tooling
and a long support window; PostgreSQL 16 (not an older major) is chosen on its
own support-window merits, not on any single SQL feature. Required extensions:

- **`pgvector`** (required) — embeddings, ANN search (HNSW preferred).
- **`pg_trgm`** (required) — trigram similarity for `entity.canonical_name`
  fuzzy/alias matching; an early migration runs
  `CREATE EXTENSION IF NOT EXISTS pg_trgm` and the index uses
  `USING gin (canonical_name gin_trgm_ops)`.
- **Timescale** (optional) — `time_series_point` may become a hypertable, but
  plain RANGE partitioning on `ts` is the default and Timescale is never required.

The single instance carries all 12 canonical tables (`source`, `crawl_job`,
`raw_document`, `document`, `summary`, `embedding`, `time_series_point`, `entity`,
`mention`, `alert`, `audit_log`, `source_health`). Concretely:

- **Job queue** lives in `crawl_job`, claimed with
  `SELECT … FOR UPDATE SKIP LOCKED`, with a `dead` status acting as the DLQ. No
  Redis/Celery/RabbitMQ/Kafka.
- **Full-text search** is a GIN `tsvector` index on `document.body_text`.
  Meilisearch (a single binary) is adopted only if Postgres FTS is genuinely
  outgrown; OpenSearch is never used.
- **Vector search** is `pgvector` with one active embedding model+dim per
  deployment; Qdrant is never used.
- **Time-series** is the RANGE-partitioned `time_series_point` table; ClickHouse
  is never used.
- **Raw blobs** are gzipped, content-addressed files on local disk
  (`RAW_STORE_DIR`), never stored in Postgres.

Deployment is Docker Compose on one VM with **exactly one scheduler instance** and
one or more workers; the scheduler's single-instance invariant is enforced
elsewhere (single compose service / systemd unit / startup advisory lock). This
ADR fixes the single-store decision; concurrency is detailed in the architecture
and runbook docs.

## Consequences

**Positive**

- Minimal operational surface: one engine to deploy, secure, back up, monitor,
  and upgrade. A nightly `pg_dump` plus an offsite sync of `RAW_STORE_DIR`
  captures the entire control + normalized state.
- Multi-table canonical writes are real transactions, so provenance stays
  consistent (no cross-service eventual-consistency gaps).
- One mental model and one connection string (`DATABASE_URL`,
  `postgresql+psycopg`) for the whole control + normalized plane.
- Cheap to run on a small VM (Hetzner default), matching the cost posture.

**Negative / costs**

- Postgres FTS and `pgvector` are good, not best-in-class, at large scale; we
  accept lower ceilings in exchange for simplicity.
- `time_series_point` mixes scalar (`value`) and OHLCV columns in one table; the
  sparse-column tradeoff is accepted for one lean schema (see ADR 0003).
- Vertical scaling and careful indexing are the only headroom levers; there is no
  horizontal sharding story, by design.

**Follow-ups / obligations**

- The Alembic harness must enable `pgvector` and `pg_trgm` extensions in an early
  migration (B1) before any dependent index is created.
- `docs/architecture/README.md` and `docs/runbooks/README.md` document the
  single-scheduler invariant, the SKIP LOCKED queue, backups, and a restore
  drill (B14).
- The "freeze scope until the current set runs unattended for a month" discipline
  (spec section 8) is the guardrail that keeps this decision intact.

**Revisit triggers**

- Postgres FTS demonstrably outgrown → adopt Meilisearch (single binary) only,
  documented in a new ADR.
- A real, present need for an entity graph → consider a graph store (needs a
  documented reason and a new ADR), never speculatively.
- Sustained ingestion volume that one box cannot serve unattended → reopen with a
  new ADR; scaling out is not assumed until proven necessary.
