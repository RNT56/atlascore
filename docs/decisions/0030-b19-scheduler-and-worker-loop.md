# 0030. B19 single-instance scheduler + continuous worker loop

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Operator (single-operator project)
- **Spec reference:** CLAUDE.md §6 (canonical flow, single-scheduler invariant), §17 (reliability/concurrency), §9 (CLI); post-MVP cycle B19 (wires B16/B17/B18 onto a cadence). Builds on the B4 fetch role + B5/B11 parse/enrich + B8 freshness + B13/B18 alerts + B16 retention + B17 partitions.

## Context

The MVP shipped every pipeline stage (`run_one_fetch/parse/enrich`, `refresh_source_health`,
`run_alerts`, `run_retention`, `ensure_yearly_partitions`) but only as one-shot CLI commands — there
was no unattended driver. B19 adds the two long-running daemons that make AtlasCore run on its own,
**reusing** the existing functions (no pipeline rewrite, no migration). The forced decisions:

1. **Single-scheduler invariant** (two schedulers double-fire) — how to enforce it.
2. **Where periodic system-wide tasks run.** `crawl_job.source_id` is `NOT NULL`, so freshness /
   alert / partition / audit jobs (which have no per-source row) **cannot** be queued without making
   `source_id` nullable (a migration). B19 is migration-free.
3. **Idempotent enqueue** across overlapping scheduler ticks.

## Decision

- **Two roles, two locks.** `atlas scheduler run` holds a dedicated scheduler advisory lock
  (`scheduler.lock`, key `0x41544C53_53434844`) — a second instance is refused with
  `SchedulerBusyError` (mirrors the B4 fetch lock, distinct key). `atlas worker run` drains the
  per-item pipeline; its **fetch** role holds the existing single-fetch lock (R16); parse/enrich
  scale out lock-free.
- **Scheduler enqueues only per-source fetch jobs** (they have a `source_id`), one per due cron
  (`source.config['schedule']`, APScheduler `CronTrigger`, strictly-after semantics) with a
  **tick-bucketed** `dedupe_key` (`fetch:<slug>:sched:<floored-now>`) so overlapping ticks are
  idempotent. Default-deny: only fetchable (approved + enabled) sources.
- **System-wide housekeeping runs INLINE in the scheduler** on independent cadence buckets —
  freshness (`refresh_source_health`), alert (`run_alerts` evaluate+deliver), partition
  (`run_partition_maintenance`), audit (`run_retention`). The scheduler is single-instance, these
  are cheap control-plane ops, and inline avoids the nullable-`source_id` migration. (`JobType.
  PARTITION` from B17 remains available but the scheduler runs partition maintenance inline rather
  than queuing it.)
- **Worker loop** = claim one job per enabled role per pass; loop immediately while there is work,
  else sleep with a bounded idle backoff (`stop_event.wait`, capped by
  `worker_idle_backoff_max_seconds` — `min(backoff, cap)` so even the first idle sleep is bounded,
  keeping tests fast). Stale claims are periodically reclaimed (`reclaim_stale`). The fetcher is
  dependency-injected for tests (stub resolver); production builds an httpx-backed `SafeFetcher`.
- **Graceful shutdown.** SIGINT/SIGTERM set a `stop_event` (best-effort off the main thread); both
  daemons exit cleanly and release their locks. One bad scheduler tick is caught and logged — it
  never kills the daemon (§18).
- **Real liveness.** `atlas health`/`atlas digest` now report `scheduler_active()` (via `pg_locks`),
  replacing the B8 placeholder. Both stay MCP-independent (§17).

## Consequences

- **Positive.** AtlasCore now runs unattended from two systemd units (or compose services): the
  scheduler enqueues + housekeeps, the worker drains. The single-scheduler + single-fetch invariants
  are enforced at startup. No migration; all pipeline logic is reused. Enqueue is idempotent across
  ticks. Tested end-to-end (a bounded loop drains fetch→parse→enrich to documents).
- **Negative / accepted.** Inline housekeeping means those tasks run only while the scheduler is up
  (acceptable — the scheduler is the control plane). Cadence state for the audit task is in-memory,
  so a scheduler restart may re-run rotation once (idempotent). The worker drains one job per role
  per pass (fair, simple) rather than greedily per role — fine at single-operator scale.
