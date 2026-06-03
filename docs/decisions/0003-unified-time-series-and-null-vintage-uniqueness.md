# 0003. Unified `time_series_point` with NULLS NOT DISTINCT vintage uniqueness (R1)

- **Status:** Accepted
- **Date:** 2026-06-03
- **Deciders:** Operator (single-operator project)
- **Spec reference:** AtlasCore Canonical Decisions Spec v1 (2026-06-03), section 2.7, resolution R1; correction notes (1) composite PK, (2) PG15 attribution

## Context

AtlasCore ingests two shapes of numeric series: revisable macro **observations**
(e.g. FRED releases, where a value for a date can be revised and republished with
a later vintage/as-of) and market **bars** (OHLCV). The lean, single-box posture
(ADR 0001) argues against two parallel tables and two parallel query paths when
one table with a discriminator will serve single-operator scale.

Two correctness problems must be solved for that single table:

1. **Idempotent upserts with a nullable vintage.** Macro observations carry an
   optional `vintage` (data-vintage / as-of). The common case is `vintage IS
   NULL` (non-revisable or latest-only series). A normal `UNIQUE (provider,
   series_id, ts, vintage)` constraint treats two NULL `vintage` values as
   *distinct* (SQL's default NULL semantics), so replaying the same observation
   would insert a duplicate instead of conflicting — breaking the
   "idempotent, safe to replay" worker invariant.

2. **Unique/PK constraints on a partitioned table.** `time_series_point` is RANGE
   partitioned on `ts` for retention and query locality. PostgreSQL **rejects**
   any unique or primary-key constraint on a partitioned table unless it
   **includes every partition-key column** ("unique constraint on partitioned
   table must include all partitioning columns"). A surrogate `PRIMARY KEY (id)`
   alone is therefore **not creatable** on this table.

A factual note on the chosen mechanism: `UNIQUE NULLS NOT DISTINCT` was
introduced in **PostgreSQL 15**, not 16. AtlasCore baselines on PostgreSQL 16
(ADR 0001) for its support window, which comfortably includes this feature; the
feature itself must not be misattributed to 16.

## Decision

**This is resolution R1.** AtlasCore keeps **one unified `time_series_point`
table** for both macro observations and market bars, RANGE partitioned on `ts`,
with the following uniqueness and key design:

**Discriminator and columns** (per spec 2.7): `kind` discriminates
`observation` vs `bar`. Macro observations populate `value`; market bars populate
`open`/`high`/`low`/`close`/`volume` (nullable, only for `kind = bar`). Also:
`provider`, `series_id`, `ts timestamptz`, `unit`, `vintage timestamptz`
(nullable; as-of for revisable macro series), `schema_version`. The sparse-column
tradeoff (some columns null per row depending on `kind`) is accepted for one lean
table.

**Composite primary key (correction 1):** the surrogate key is
**`PRIMARY KEY (id, ts)`**, not `PRIMARY KEY (id)`, because `ts` is the partition
key and PostgreSQL requires every unique/PK constraint on a partitioned table to
include all partitioning columns. **This rule applies to any future partitioned
table in AtlasCore**: its surrogate PK must include the partition key column(s).

**Idempotency uniqueness (R1):**
`UNIQUE NULLS NOT DISTINCT (provider, series_id, ts, vintage)`. Because `ts` is
the partition key, this unique constraint already includes the partition column
and is valid on the partitioned table. `NULLS NOT DISTINCT` makes two NULL
`vintage` values **conflict** (treated as equal), so re-ingesting the same
observation upserts onto the existing row instead of duplicating it.

**Version note (correction 2):** `NULLS NOT DISTINCT` requires **PostgreSQL
15+**. AtlasCore's PostgreSQL 16 baseline is justified by its support window
(ADR 0001), not by this feature.

**Pre-15 fallback (kept for portability):** if ever run on PostgreSQL < 15, the
equivalent is a unique index over a generated key
`vintage_key = coalesce(vintage, 'epoch')`, i.e.
`UNIQUE (provider, series_id, ts, vintage_key)`. This is a documented fallback
only; the canonical baseline uses `NULLS NOT DISTINCT`.

**Partitioning and indexes:** RANGE partition on `ts` (monthly or yearly), with
an optional LIST sub-partition by `provider`; Timescale hypertable optional. The
unique constraint above plus `btree (provider, series_id, ts)` are the canonical
indexes. Vintages are retained (revision history is meaningful for macro series).

This unified table powers the `get_macro_series` MCP tool and the FRED connector
(B9).

## Consequences

**Positive**

- One table and one query path for all numeric series; one set of partitioning,
  retention, and indexing rules.
- Upserts are genuinely idempotent for the dominant `vintage IS NULL` case, so
  worker replays and backfills never create duplicate observations.
- Revisable macro data keeps full vintage history without a separate revisions
  table.
- The schema is actually creatable: the composite PK satisfies PostgreSQL's
  partitioned-table constraint rule.

**Negative / costs**

- Sparse columns: `value` is null for bars, OHLCV is null for observations.
  Accepted for leanness; readers must respect `kind`.
- `NULLS NOT DISTINCT` ties the canonical schema to PostgreSQL 15+; the pre-15
  fallback is carried only for portability and adds a generated-column code path
  if ever used.
- The composite `PRIMARY KEY (id, ts)` means `id` alone is not globally unique by
  the PK; callers needing a single-column lookup must include `ts` or rely on the
  business unique key.

**Follow-ups / obligations**

- The Alembic migration creates `time_series_point` as a partitioned parent with
  `PRIMARY KEY (id, ts)` and the `UNIQUE NULLS NOT DISTINCT` constraint, and
  provisions partitions.
- Any future partitioned table follows the same composite-PK rule; note this in
  `docs/schemas/README.md`.
- The FRED connector (B9) writes via upsert on the business unique key and is
  proven idempotent under replay in tests.

**Revisit triggers**

- Time-series volume that one box cannot serve unattended → reconsider Timescale
  hypertable adoption or a dedicated store (new ADR; see ADR 0001 revisit).
- A need to run on PostgreSQL < 15 → switch to the `vintage_key` fallback index
  (documented, no behavior change).
