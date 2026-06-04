# 0018. B8 freshness/health: `source_health`, the freshness computation, `get_source_health`, and the digest

- **Status:** Accepted
- **Date:** 2026-06-04
- **Deciders:** Operator (single-operator project)
- **Spec reference:** AtlasCore Canonical Decisions Spec v1 (2026-06-03), §2.12 `source_health` / R7, §13/§2.5 (`get_source_health`), §17 (freshness end-to-end, single-scheduler), docs/runbooks §8/§12; MVP block **B8** (builds on B7 / ADR 0017).

## Context

B8 builds the freshness/health subsystem: detect sources that go silently stale or start failing,
surface them to the operator, and mirror the snapshot read-only to agents. The forced decisions:

1. **What `source_health` stores and how status is derived.** §2.12 defines a current-snapshot
   table (PK = `source_id`) with `freshness_lag` (`interval`) classified against each source's
   `freshness` thresholds, plus quality metrics. The classification precedence and the
   circuit-breaker trip point had to be pinned.
2. **Who computes vs who reads.** §17 says the `job_type=freshness` job computes `source_health`
   and `atlas health`/`atlas digest` read it — but the **scheduler does not exist yet** (a later
   block), so nothing would populate the table on a real box.
3. **Where the digest is delivered.** §12 says the digest emits "over the alert delivery channel"
   — but that channel (webhook/ntfy/email) is built in **B13**.
4. **How `get_source_health` stays safe.** §2.5 requires no secrets and no internal hostnames in
   the output.

## Decision

B8 implements `atlascore.schemas.source_health`, `storage.postgres.models.source_health`
(migration `0005`), `atlascore.worker.freshness`, `storage.source_health`, the `get_source_health`
MCP tool, and `cli/health.py` (`atlas health` / `atlas digest`):

- **`source_health` table.** PK `source_id` (FK `ON DELETE RESTRICT`, R17), `freshness_lag` as a
  Postgres `interval`, `status` CHECK-bounded to `ok|warn|stale|error|cooling_down`, quality
  metrics as nullable `numeric`, one `btree(status)` index. Current snapshot only (no history).
- **Pure status classification (unit-tested).** `classify_status(lag, consecutive_failures, *,
  warn_after, error_after, cooling_threshold)` with precedence: `cooling_down` (≥
  `COOLING_THRESHOLD = 3` consecutive failed fetches) > `stale` (lag > `error_after`) > `warn`
  (lag > `warn_after`) > `error` (has failed and never produced data) > `ok`. `freshness_lag =
  now - coalesce(last_item_published_at, last_success_at)`; thresholds are parsed from the source's
  `freshness` jsonb (missing/invalid thresholds simply don't drive a lag-based status).
  `consecutive_failures` = the trailing run of `dead` fetch jobs since the last `done`.
- **CLI drives the computation on demand.** `refresh_source_health(session, *, now,
  source_id=None)` is the freshness job's core (read-only compute over `crawl_job` + `document`,
  then idempotent upsert). Because there is no scheduler yet, **`atlas health` and `atlas digest`
  recompute `source_health` first, then read it** — the pragmatic single-box behavior. When the
  scheduler lands it will call the same `refresh_source_health` on a timer; `atlas health`/`digest`
  remain CLI-driven and **MCP-independent** (§17 / architecture §7), so the control plane never
  depends on MCP.
- **Digest delivery seam.** `build_digest(...)` renders the text (non-ok sources + DLQ depth +
  fetch-worker liveness); `deliver_digest(...)` prints it to stdout and logs it. **B13 swaps in**
  the real `ALERT_WEBHOOK_URL`/`NTFY_TOPIC`/SMTP transport without changing the digest content.
- **`get_source_health` (MCP, second tool).** Reuses `list_source_health`, which joins only the
  **non-secret** source identity (name + slug) — never `base_url`, `credential_ref`, or an
  internal endpoint. Read-only, capped (`limit ≤ 200`), audited per call via `write_audit_log`
  (B7). It is a convenience mirror, **not** the operational gate.
- **Honest metrics.** Freshness-critical fields are computed precisely; `success_rate_24h`,
  `parse_quality`, and `item_count_recent` use simple rolling-24h definitions; `duplicate_rate` is
  **null in MVP** (per-item dedupe outcomes are not yet recorded) rather than fabricated.
- **Liveness.** `fetch_worker_active` inspects `pg_locks` for the fetch advisory lock (B4) without
  acquiring it. The single-scheduler liveness check is deferred until the scheduler exists.

## Consequences

- **Positive:** a stale or failing source provably surfaces in `source_health`, in `atlas health` /
  `atlas digest`, and (read-only) via `get_source_health` — verified live and over real stdio. The
  classifier is pure and unit-tested; the freshness computation is reusable as the future scheduled
  job; `get_source_health` leaks no secrets/hostnames and is audited like every MCP call.
- **Negative / costs:** `atlas health`/`digest` recompute on each run (cheap at single-operator
  scale, but it couples read with compute until the scheduler arrives — a documented, intentional
  pragmatism, not the eventual steady state). `duplicate_rate` is always null for now; the
  scheduler-liveness half of the digest is a placeholder; the digest only prints (no real channel
  until B13). The freshness **alert** (stale/error → alert) is not fired yet — the alert pipeline
  is B13.
- **Follow-ups / obligations:** B9 (FRED + `get_macro_series`) is next. The scheduler block wires
  `refresh_source_health` as a timed `job_type=freshness` job and adds the single-scheduler
  liveness check. B13 fires freshness alerts and provides the real digest delivery channel +
  per-item dedupe metrics (`duplicate_rate`). All later MCP tools keep reusing `write_audit_log`.
- **Revisit triggers:** the scheduler lands (move compute off the CLI read path; add scheduler
  liveness); a real need for `duplicate_rate` / richer parse-quality metrics; health history is
  wanted (the MVP keeps only the current snapshot).
