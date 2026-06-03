# 0005. Freshness target, end to end (R7)

- **Status:** Accepted
- **Date:** 2026-06-03
- **Deciders:** Operator (single-operator project)
- **Spec reference:** AtlasCore Canonical Decisions Spec v1 (2026-06-03), sections 2.1, 2.12, 4, 5, 8 (B7/B8), 9, resolution R7; corrections (10), (11)

## Context

AtlasCore must run unattended for weeks and **tell the operator when something
breaks** — especially the silent failure mode where a feed simply stops producing
new items while every fetch still returns HTTP 200. A bare "freshness target"
number on a source is useless unless it is computed, compared, surfaced, and
delivered. The decision must therefore wire freshness **end to end**: declaration
→ computation → state → operator surface → delivery, without the control plane
depending on MCP (architecture rule: ingestion/control plane never depends on
MCP; agents never trigger crawls).

## Decision

**This is resolution R7.** Freshness is implemented as a complete loop across five
pieces:

**1. Declaration — `source.freshness jsonb` (spec 2.1, 4).** Each source declares
structured thresholds in its git-tracked YAML, mirrored into the `source` table:

```yaml
freshness:
  target: "24h"        # expected max age of newest item / max interval between successful fetches
  warn_after: "36h"    # -> source_health.status = warn; appears in daily digest
  error_after: "72h"   # -> status = stale/error; fires a freshness alert
```

Duration grammar matches retention (`Nd | Nw | Nm | Ny`; here also `Nh`).

**2. State — `source_health` table (spec 2.12).** One current-snapshot row per
source (`source_id` PK/FK), holding `last_attempt_at`, `last_success_at`,
`last_item_published_at`, `freshness_lag interval`, `status (ok | warn | stale |
error | cooling_down)`, `consecutive_failures`, `success_rate_24h`,
`parse_quality`, `duplicate_rate`, `item_count_recent`, `updated_at`. No history
table in MVP. Definition:
`freshness_lag = now - coalesce(last_item_published_at, last_success_at)`.

**3. Computation — the freshness job (`crawl_job.job_type = freshness`).** Run on
a schedule, it recomputes each source's `freshness_lag` and maps it onto
thresholds: lag ≤ `target` → `ok`; lag ≥ `warn_after` → `warn`; lag ≥
`error_after` → `stale`/`error` and **fires a freshness alert** (`alert` row).
Circuit-breaking remains separate: a source with too many consecutive failures is
`cooling_down`.

**4. Operator surface — CLI digest (control-plane, MCP-independent).** Per
correction (11), an **`atlas health` / `atlas digest`** CLI command reads
`source_health` plus the `crawl_job` DLQ depth (status = `dead`) and a
scheduler/worker liveness check, then **emits the daily digest** via the alert
delivery channel (`ALERT_WEBHOOK_URL` / `NTFY_TOPIC` / SMTP). **Health is
CLI-driven**: this command is the operational gate, not MCP.

**5. MCP read convenience — `get_source_health`.** The MCP tool exposes
freshness/error/parse-quality per source for agents (no secrets, no internal
hostnames). It is **read-only convenience, not the operational health gate** —
the control plane never depends on MCP.

**Build ordering (correction 10).** Because `get_source_health` reads
`source_health` and any tool needs audit logging, the dependency is made explicit:

- **B7** ships `search_documents`, **creates the `audit_log` table**, and adds
  result caps. (B7 does **not** ship `get_source_health`.)
- **B8** adds the `source_health` table, the freshness job, the daily digest CLI
  command, **and** `get_source_health`.

This removes the original B7→`source_health` build-order violation.

## Consequences

**Positive**

- Silent staleness becomes loud: a feed that stops producing surfaces in
  `source_health.status` and in the delivered daily digest.
- The operator's health surface (`atlas health`/`atlas digest`) lives entirely in
  the control plane, honoring "control plane never depends on MCP."
- Structured thresholds make warn vs error behavior unambiguous and per-source
  tunable.

**Negative / costs**

- `source_health` is a current snapshot only (no trend history in MVP); trend
  analysis would need a future history table.
- An extra scheduled job (`job_type = freshness`) and a CLI/delivery path to
  maintain.

**Follow-ups / obligations**

- B8 creates `source_health`, the freshness job, `atlas health`/`atlas digest`,
  and `get_source_health`; B7 creates `audit_log` and result caps first.
- Delivery uses the same channel as alerts; configure via env (section 7).
- `docs/runbooks/README.md` documents reading the digest and acting on
  `warn`/`stale`/`cooling_down` sources; `docs/mcp/README.md` notes
  `get_source_health` is read-only convenience.

**Revisit triggers**

- A need for freshness trends/SLO history → add a `source_health_history` table in
  a new ADR.
- Adding a second fetch worker changes liveness semantics → coordinate with the
  fetch single-instance guard (separate resolution R5/correction 16).
