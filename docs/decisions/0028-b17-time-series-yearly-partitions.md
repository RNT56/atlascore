# 0028. B17 time-series yearly partitions + partition maintenance

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Operator (single-operator project)
- **Spec reference:** docs/schemas §2.7 (`time_series_point` partitioning), CLAUDE.md §11.7/§12; post-MVP cycle B17 (builds on migration 0006).

## Context

Migration 0006 created the RANGE-by-`ts` parent with a single DEFAULT catch-all partition; STATUS
listed yearly partitions as a deferred refinement. The forced decisions:

1. **Attaching a bounded child when DEFAULT already holds rows** — Postgres rejects a bare ATTACH
   (overlap), so the carve must move rows.
2. **`JobType.PARTITION`** for the scheduler's maintenance job — but `crawl_job.job_type` has a DB
   CHECK (`ck_crawl_job_job_type_allowed`, migration 0002) listing only fetch/parse/enrich/alert/
   freshness, so adding the value needs an ALTER.
3. **Keeping partitions out of the autogenerate drift-check.**

## Decision

- **Migration 0010 carves the deploy-time current + next calendar year** into bounded yearly
  children via the **detach-DEFAULT → create-bounded-child → move-matching-rows → re-attach-DEFAULT**
  dance (with-rows safe), in one transaction, idempotent (guarded by `to_regclass`). The parent's
  composite PK `(id, ts)` + `UNIQUE NULLS NOT DISTINCT` natural key are inherited by children
  unchanged; DEFAULT stays as the catch-all for any uncarved year. `downgrade` collapses every
  yearly child back into DEFAULT (detach child → reinsert via parent → drop) and restores the CHECK.
- **The same migration widens `ck_crawl_job_job_type_allowed`** to accept `'partition'` (DROP +
  re-ADD); `JobType.PARTITION` is added to the enum so the model's CHECK text matches. `downgrade`
  deletes any `partition` jobs then restores the 5-value CHECK.
- **`storage.partitions.ensure_yearly_partitions`** is the runtime, idempotent version of the carve
  (current..current+N), reused by `worker.partitions.run_partition_maintenance` (under a dedicated
  `partition` advisory lock so two runners can't race the detach/attach) and by
  `atlas tsp partitions ensure|status`. The B19 scheduler enqueues a monthly `JobType.PARTITION` job;
  until then it is operator-run.
- **No drift-check change needed:** children are named `time_series_point_<year>`, already covered by
  `migrations/env.py:_is_raw_sql_table`'s `time_series_point` prefix (a clarifying comment was added).
  Year is bounded `[2000, 2100]` before it is interpolated into a table identifier (injection-safe).

## Consequences

- **Positive.** Time-series is now yearly-partitioned for prune/query at volume; reads/writes
  (`upsert_observations`/`get_macro_series`) route transparently across partitions; the migration
  round-trips on live PG16; rows already in DEFAULT are correctly relocated (with-rows regression);
  `JobType.PARTITION` is ready for the B19 scheduler.
- **Negative / accepted.** The carve takes a brief ACCESS EXCLUSIVE lock window (fine at
  single-operator scale; run off-peak). Cadence is operator/CLI-driven until B19. The with-rows move
  is the one routine that relocates time-series rows (raw_document blobs remain immutable). The
  `atlas tsp partitions ensure --years-ahead N` can create far-future bounded partitions on demand
  (harmless empty catch-alls).
