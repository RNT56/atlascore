# 0023. B13 alerts: `alert` table + four rule-based alerts + delivery channel + `get_alerts`

- **Status:** Accepted
- **Date:** 2026-06-04
- **Deciders:** Operator (single-operator project)
- **Spec reference:** AtlasCore Canonical Decisions Spec v1 (2026-06-03), docs/schemas §2.10 `alert`, docs/mcp §2.6 + §3 `get_alerts` pipeline, CLAUDE.md §11.10 / §13 / §17 (freshness alert); MVP block **B13** (builds on B5 documents, B9 time-series, B8 source_health, B7 MCP/audit).

## Context

B13 adds the alert subsystem: rule evaluation → idempotent persistence → delivery → a read-only MCP
mirror. The forced decisions:

1. **What is "new" for a rule, and how to stay idempotent.** Re-evaluating must never double-fire.
2. **`alert` is a normal table** (unique `dedupe_key` + a btree) — unlike the raw-SQL
   enrichment/summary tables, it has no GIN/partition/vector index.
3. **Evidence for non-document alerts.** `macro_release` (a time-series observation) and the
   `freshness` alert (a `source_health` row) have no backing `document`, but the spec's evidence
   field is `evidence_doc_ids[]`.
4. **Delivery must be testable offline** and should also close B8's stdout-only digest seam.
5. **Whether to ship a `freshness` alert** (CLAUDE.md §17 mandates one; B8 left the seam) given the
   done-when names only the three content alerts.

## Decision

- **`alert` table via `op.create_table` (migration `0009`), autogenerate/drift-checked** — the
  normal-table path (like `source_health`/`audit_log`), not the raw-SQL path. `dedupe_key` is
  `unique`; `btree(status, severity, created_at)` serves `get_alerts`; `evidence_doc_ids` is a
  non-referential `text[]`; `detail` is sanitized via `sanitize_for_storage` before write (§14).
- **Idempotency via a deterministic `dedupe_key` + `ON CONFLICT DO NOTHING`.** Rules are pure
  readers that scan recent processed data and emit candidates; the worker upserts each on its
  `dedupe_key`, so re-evaluation is a no-op. No "since" cursor is needed — the dedupe key *is* the
  identity. Keys: `sec_filing_new:{doc_id}`, `cve_watch_new:{doc_id}`,
  `macro_release_new:{provider}:{series_id}:{ts}` (a new release ⇒ new `ts` ⇒ new alert),
  `freshness_stale:{source_id}:{status}`.
- **Four rules** (`alerts.rules`), evaluated by the alert worker (the spec's `job_type=alert`):
  `sec_filing` + `cve_watch` scan `document`s by source **category** (`finance_filings` /
  `security_cve`) and cite the document in `evidence_doc_ids`; `macro_release` fires on the **latest
  observation per FRED series** (DISTINCT ON), with evidence in `detail` (no document);
  `freshness` reads `source_health` for `stale`/`error`/`cooling_down` and fires per source, also
  with evidence in `detail`. **The `freshness` rule is included** — it closes the explicit B8/§17
  seam and is small; `get_alerts` accepts `freshness` as a fourth `alert_types` value (a documented
  superset of the docs/mcp §2.6 three-type sketch). Macro/freshness alerts honestly carry empty
  `evidence_doc_ids` (the done-when's "evidence" is the cited document for sec/cve and the `detail`
  payload for macro/freshness).
- **One configured delivery channel** (`alerts.delivery`): `ALERT_WEBHOOK_URL` → webhook, else
  `NTFY_TOPIC` → ntfy, else SMTP (`SMTP_HOST`+`SMTP_TO`), else a **`LogChannel`** fallback
  (stdout/log). Delivery endpoints are operator-configured **trusted config** (like
  `DATABASE_URL`), not agent-supplied, so the SSRF guard (§14, for fetched/agent URLs) does not
  apply. The same channel now backs **`atlas digest`**, closing B8's stdout-only seam — `build_digest`
  text goes over the channel (and is still echoed locally). `LogChannel` makes the whole pipeline
  testable offline; a webhook contract test uses `respx`.
- **`get_alerts` is read-only** (docs/mcp §2.6 / §3): filters by `alert_types` / `status` (default
  `open`+`acknowledged`) / `min_severity` (ordered `info<low<medium<high<critical`) / `since`,
  caps at 100, resolves `evidence_doc_ids` to document/source provenance (a purged doc drops out),
  and is audited. It **cannot** create/acknowledge/resolve/deliver — those are CLI/pipeline
  operations (`atlas alerts run`).

## Consequences

- **Positive.** The done-when (each alert fires with dedupe + evidence and is queryable via MCP)
  works end to end and is fully idempotent; the delivery channel + digest wiring close the B8 seam;
  `get_alerts` cites evidence so an agent can pull the underlying documents. The control plane
  (`atlas alerts run`) never depends on MCP.
- **Negative / accepted (see STATUS Known issues).** Rules fire on *any* matching item in the scan
  window (no per-issuer/CVSS watchlist yet — every filing/CVE alerts, not just "watched" ones; the
  spec's `nvd_critical_watch`-style filtering is a later refinement). The `freshness` dedupe is per
  `(source, status)`, so a flap back into the same status won't re-alert until the row is pruned.
  Lifecycle transitions (`acknowledge`/`resolve`) have no CLI yet. Delivery is fire-and-record
  (a failed send stays undelivered for the next run; no backoff). Alert evaluation runs via
  `atlas alerts run`; the timed `job_type=alert` lands with the scheduler block.
