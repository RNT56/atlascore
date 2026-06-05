# 0027. B16 retention: audit_log rotation + crawl_job pruning (no new JobType)

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Operator (single-operator project)
- **Spec reference:** CLAUDE.md §11.2 (crawl_job retention 90d), §11.11 + R14 (audit retention_class), §16 (retention classes); first block of the post-MVP cycle (B16).

## Context

The MVP left a Known Issue: routine `audit_log` rows and terminal `crawl_job` rows accumulate
forever (no rotation job). R14 already gives `audit_log.retention_class` (`routine`|`purge`); the
`crawl_job` status machine + `finished_at`/`created_at` already exist. So this is pure DML + config +
CLI — no schema change. The forced decisions:

1. **How to delete audit rows without ever touching erasure proof** (R14).
2. **How to prune crawl jobs without deleting live work.**
3. **Whether to add a `JobType='retention'`** (would require a CHECK-constraint migration) or run
   rotation as control-plane housekeeping.

## Decision

- **`storage.retention` (pure DML, injected `now`).** `rotate_audit_log` deletes only
  `retention_class='routine'` rows older than `now - window` — **filtered on `retention_class`,
  never on action strings** — so `purge` rows are structurally untouchable (R14). It also reports
  `purge_retained_under_cutoff` (a proof metric). `prune_crawl_jobs` deletes only **terminal**
  (`done`/`dead`) rows older than `now - window`, keyed on `coalesce(finished_at, created_at)`;
  `pending`/`claimed`/`running`/`failed` are never pruned regardless of age. `run_retention`
  orchestrates both in the caller's transaction and writes **one retained** (`retention_class=
  'purge'`) bookkeeping entry with counts only — never row content — mirroring the `atlas purge`
  retained-audit pattern (so the rotation itself is provable and never rotated away).
- **No new `JobType`.** Rotation is control-plane housekeeping, not a fan-out queue job, so it adds
  no enum value (which would force a `ck_crawl_job_job_type_allowed` CHECK migration). The CLI
  `atlas audit rotate [--dry-run] [--audit-window] [--crawl-window]` is the shippable surface; the
  B19 scheduler calls `run_retention(...)` directly on a daily cadence (a one-line inline call, not
  a queued job).
- **Two config knobs** in `Settings` (`AUDIT_ROUTINE_RETENTION=180d`, `CRAWL_JOB_RETENTION=90d`)
  using the existing duration grammar (`parse_duration`, no `permanent` — rotation needs a finite
  cutoff).

## Consequences

- **Positive.** The retention Known Issue is closed with no migration; R14 is enforced structurally
  and regression-tested (an ancient `purge` row survives); live work is never pruned; `--dry-run`
  makes it safe to preview; idempotent. The rotation is auditable via its own retained entry.
- **Negative / accepted.** The retained bookkeeping entries accumulate (one tiny count-only `purge`
  row per non-dry-run run) — by design (erasure-proof rows are retained); a future digest could
  surface the count. A first rotation of a long-unrotated table is one large DELETE — fine at
  single-operator scale (run off-peak); a batched-delete refinement is possible later. Cadence is
  operator-driven (cron/CLI) until B19's scheduler runs it daily.
