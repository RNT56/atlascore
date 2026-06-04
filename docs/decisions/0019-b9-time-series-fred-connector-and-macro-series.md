# 0019. B9 macro time-series: the partitioned `time_series_point`, the FRED connector, and `get_macro_series`

- **Status:** Accepted
- **Date:** 2026-06-04
- **Deciders:** Operator (single-operator project)
- **Spec reference:** AtlasCore Canonical Decisions Spec v1 (2026-06-03), §2.7 `time_series_point` / R1 + composite-PK rule, §13/§2.2 (`get_macro_series`), §10 (connector lifecycle), §14 (secret handling), §15 (no zero-fill); MVP block **B9** (builds on B7/B8).

## Context

B9 adds the first non-document source (FRED macro series) and the first **partitioned** table.
Forced decisions:

1. **Partitioned `time_series_point`.** §2.7 requires RANGE partitioning on `ts`, a composite PK
   `(id, ts)` (Postgres rejects a PK that omits the partition key), and a `NULLS NOT DISTINCT`
   natural key (PG15+). Alembic autogenerate cannot emit `PARTITION BY`.
2. **A non-document connector.** The connector ABC was RSS/document-shaped (`fetch` +
   `parse_entries`). FRED produces time-series, not feed entries/documents, and the observations
   payload does not echo the `series_id`.
3. **Per-source connector config.** The series id (and human unit label) must live in the source
   registry, but the YAML/`SourceConfig` had `extra="forbid"` with no slot for it.
4. **The FRED api key.** FRED only supports `?api_key=…` query auth, so the key is in the request
   URL — it must never reach a log, error column, or backup (§14).
5. **Where macro ingest runs.** The single fetch worker claims any fetch job; RSS fans out per-GUID
   parse jobs, but macro has no per-item fan-out.

## Decision

B9 implements `storage.postgres.models.time_series_point` (migration `0006`),
`connectors.fred.series`, `worker.macro`, `storage.timeseries`, the `get_macro_series` MCP tool,
and small SDK/schema/secrets additions:

- **`time_series_point` via raw SQL (migration 0006).** Parent `PARTITION BY RANGE (ts)` with a
  composite PK `(id, ts)`, a `kind` CHECK, and `CONSTRAINT … UNIQUE NULLS NOT DISTINCT (provider,
  series_id, ts, vintage)`; plus a **DEFAULT partition** that catches every `ts` (MVP single-operator
  scale — yearly partitions are a documented later refinement). The ORM model maps columns for
  querying only; all `time_series_point*` objects are **excluded from the autogenerate drift-check**
  by a name-prefix rule in `_include_object` (verified: drift-check empty, the auto-named partition
  indexes are covered by the prefix). **The composite-PK rule now applies to any future partitioned
  table.**
- **Connector ABC made multi-shape.** `parse_entries` and `parse_observations` are both regular
  methods that **default to `NotImplementedError`** (only `fetch` stays abstract); each connector
  overrides the stage its data needs. `parse_observations(content, *, series_id)` takes the id
  because the payload omits it. `FetchContext` gains `params` (connector config) and `credential`
  (the resolved secret). `FredConnector` builds the observations URL and parses observations into
  `Decimal` values; a FRED `"."` becomes `None` (never zero-filled, §15).
- **`params` on the source registry.** `SourceFetch.params` / `SourceConfig.params`
  (`dict[str,str]`, default `{}`) carry non-secret connector config (`series_id`, optional `unit`
  label) and round-trip into `source.config` (R9). Secrets still resolve only from env via
  `credential_ref`.
- **`resolve_credential(ref)`** reads the env key named by `credential_ref` (never logged/stored).
  The api-key URL is contained: nothing persists the request URL, and the fetch worker routes every
  error string through `sanitize_for_storage` before logging — the integration test asserts the key
  never reaches `crawl_job`.
- **Macro ingest dispatched by the single fetch worker.** `run_one_fetch` claims any fetch job and
  dispatches on the claimed source's `access_method`: RSS → the document path, **API → `worker.macro`
  (fetch → store raw → parse → idempotent `upsert_observations`)**, inline (a series is small; no
  fan-out). The natural-key upsert makes re-fetching a no-op for unchanged values and an in-place
  overwrite for revisions. A deferred import breaks the `run`↔`macro` cycle.
- **`get_macro_series` (third MCP tool).** Returns the series + ordered (ascending `ts`) points with
  each `value` as a **string** (preserves `numeric` precision), missing values null, capped at
  `limit ≤ 2000`, audited (`target_ref = provider:series_id`). An un-ingested series returns
  `found=false` + a reason — never a fabricated value.

## Consequences

- **Positive:** macro series are queryable via MCP (done-when met, verified live + over stdio); the
  first partitioned table is in place with the composite-PK/`NULLS NOT DISTINCT` invariants proven;
  the connector model now cleanly supports document and time-series families; idempotent upserts
  make re-fetching safe; the api key is provably contained; `params` unblocks B10's SEC/NVD config.
- **Negative / costs:** the human **unit label** comes from `params.unit` (operator-provided) because
  the observations endpoint omits it — the `/fred/series` metadata fetch is a follow-up; **`vintage`
  is always null** (default latest; ALFRED vintaged fetching deferred); a single **DEFAULT partition**
  (not yearly) for MVP; macro parse runs **inline in the fetch worker** (acceptable for small series,
  unlike the scaled-out RSS parse). Making `parse_entries` non-abstract slightly weakens the ABC
  contract (a connector could implement neither — it then raises a clear error).
- **Follow-ups / obligations:** yearly partitions + a partition-management runbook at scale; the
  `/fred/series` unit-label fetch; ALFRED vintage support; the scheduler enqueues the daily FRED
  fetch; B10 (SEC EDGAR + NVD) reuses `params`, `resolve_credential`, and the access-method dispatch;
  market `kind=bar`/OHLCV rows reuse the same table when a market source is added.
- **Revisit triggers:** time-series volume outgrows a single DEFAULT partition; vintaged/revisable
  analysis is needed; a market-data (`bar`) source is added; the unit label or richer series
  metadata becomes important.
