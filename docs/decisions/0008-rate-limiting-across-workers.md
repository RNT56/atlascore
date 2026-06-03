# 0008. Enforce host-scoped rate limiting with a single fetch worker now and a Postgres token bucket later

- **Status:** Accepted
- **Date:** 2026-06-03
- **Deciders:** Operator (single-operator project)
- **Spec reference:** AtlasCore Canonical Decisions Spec v1 (date 2026-06-03), resolution R5; sections 2.1 (`source.rate_limit`), 5 (concurrency & multi-worker model), 17-equivalent reliability rules; MVP block B4.

> ADRs are immutable once Accepted. To change this decision, write a new ADR and
> mark this one *Superseded by*. Where this file and the canonical spec disagree,
> the spec wins and this ADR must be corrected.

## Context

AtlasCore must respect per-source rate limits to stay within terms of use. The
hardest commitment is **SEC EDGAR's 10 requests/second**, encoded as
`source.rate_limit = {max_requests: 10, window_seconds: 1}`. A rate limit is a
property of the **remote host**, not of any single worker, so the guarantee must
hold *across all processes that fetch from that host concurrently*.

Forces:

1. **Single-box, lean posture.** AtlasCore runs on one VM with a
   Postgres-backed `crawl_job` queue (`SELECT ... FOR UPDATE SKIP LOCKED`). It
   has no Redis, no broker, no external coordination service, and adding one to
   solve rate limiting would violate scope discipline.

2. **Workers may scale.** `parse` and `enrich` jobs are CPU-bound and benefit
   from multiple worker processes. `fetch` jobs are I/O-bound and are the ones
   bound by host rate limits.

3. **In-process token buckets are only correct within one process.** If two
   fetch workers each ran their own in-process bucket for `data.sec.gov`, the
   combined rate would be up to 2x the limit — silently violating the ToS
   commitment.

The spec's reliability requirements also demand backoff with jitter, respect for
`Retry-After`, and circuit-breaking unhealthy sources.

## Decision

This ADR encodes **resolution R5**.

**MVP posture: exactly one fetch worker.** Fetch concurrency *per host* in the
MVP is achieved by running a **single fetch worker process** that holds
**in-process per-host token buckets** keyed by hostname. Because there is one
fetcher, the in-process bucket is authoritative and the EDGAR "10 req/s across
workers" claim is true by construction. `parse` and `enrich` jobs may run in
additional worker processes; only the **fetch role** is pinned to one instance.

**Startup guard for the fetch role (review correction 16).** Mirroring the
scheduler's single-instance guard, a second fetch worker MUST refuse to start.
The fetch worker acquires a **PostgreSQL advisory lock** at startup (e.g.
`pg_advisory_lock(hashtext('atlascore:fetch-role'))`); a second fetch process
fails the lock and exits with a clear error. Equivalently/additionally a
`FETCH_WORKERS=1` assertion is enforced. The guard stays in force until the
documented token-bucket scale-up path (below) is explicitly enabled. This
prevents accidentally launching a second fetcher and silently doubling the
per-host rate.

**Token bucket behaviour.** Each bucket is sized from `source.rate_limit`
(`max_requests` over `window_seconds`). A fetch blocks/waits until a token is
available for that host. Buckets are keyed by **host**, not by source, so
multiple sources sharing a host share one bucket (correct for EDGAR's host-level
limit).

**Always honor `Retry-After`.** On `429`/`503` with a `Retry-After` header the
fetcher waits the indicated time regardless of bucket state. Transient failures
(DNS/connect/read/`408`/`429`/`5xx`) use **exponential backoff with jitter**;
most `4xx` are not retried. After N consecutive failures a source is
circuit-broken (`source_health.status = cooling_down`) and surfaced in the daily
digest (see ADR-numbered freshness/health work, B8).

**Documented scale-up path (when > 1 fetch worker is genuinely needed):**
replace the in-process bucket with a **Postgres-backed per-host token bucket**.
Two equivalent mechanisms, both single-box and broker-free:

- A `host_rate_state` table holding `(host, tokens, window_started_at,
  updated_at)`; a claiming worker refills and decrements tokens transactionally
  before fetching.
- `pg_advisory_xact_lock(hashtext(host))` around a decrement-on-claim, so only
  one worker mutates a host's allowance at a time.

Either makes the rate limit correct under fetch concurrency > 1. Enabling this
path is what releases the single-fetch-worker startup guard.

## Consequences

- **Positive:**
  - The EDGAR 10 req/s commitment (and every `source.rate_limit`) is correct
    with zero added infrastructure in the MVP.
  - Parse/enrich still scale out, so the single-fetcher constraint does not
    bottleneck CPU-bound enrichment.
  - The startup guard makes the "one fetcher" invariant enforced, not merely
    documented — a misconfiguration fails loudly instead of breaching ToS.
  - The scale-up path is pre-designed, broker-free, and single-box-consistent.

- **Negative / costs:**
  - Fetch throughput is capped at one process until the scale-up path is built;
    acceptable at personal scale.
  - Cross-process correctness depends on the startup guard being wired into the
    fetch entrypoint; if omitted, the guarantee is only as good as deployment
    discipline (hence the advisory lock, not just a docstring).

- **Follow-ups / obligations:**
  - Implement per-host in-process token buckets + `Retry-After`/backoff/jitter in
    the fetcher (B4).
  - Implement the fetch-role advisory-lock startup guard (B4) and document it in
    `docs/architecture/README.md` and `docs/runbooks/README.md`.
  - Document the `host_rate_state` / `pg_advisory_xact_lock` scale-up procedure
    in the runbooks; do not build it until > 1 fetch worker is needed.
  - Add a contract test that two fetch workers cannot both hold the fetch-role
    lock.

- **Revisit triggers:**
  - Sustained need for fetch concurrency > 1 (build the Postgres token bucket and
    lift the guard).
  - A source requiring a sub-second or burst-shaped limit the simple bucket
    cannot express.
  - Ever introducing an external coordination service (would require a new ADR;
    currently out of scope).
