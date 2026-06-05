# Architecture Decision Records (ADRs)

This directory holds AtlasCore's Architecture Decision Records. Each ADR captures
one significant decision using the **Context / Decision / Consequences** structure
(see [`0000-template.md`](0000-template.md)). ADRs are written for genuinely
architecture-changing decisions; routine choices live in the relevant `docs/`
deep-dive instead.

**Conventions**

- One decision per file, named `NNNN-kebab-case-title.md`.
- Status is one of: `Proposed`, `Accepted`, `Superseded by NNNN`, `Deprecated`.
- ADRs are immutable once `Accepted`. To change a decision, write a new ADR and
  mark the old one `Superseded by`.
- Every ADR is consistent with the **AtlasCore Canonical Decisions Spec v1**
  (2026-06-03); where an ADR and the spec disagree, the spec wins and the ADR is
  corrected.
- Add a row to the index below whenever you add an ADR.

## Index

| ADR | Title | Status | Date | Spec resolution |
|-----|-------|--------|------|-----------------|
| [0001](0001-single-postgres-one-box.md) | Single PostgreSQL instance on one box | Accepted | 2026-06-03 | — |
| [0002](0002-document-current-state-reproducible-not-retained.md) | `document` holds current state; old parses reproducible, not retained | Accepted | 2026-06-03 | R2 |
| [0003](0003-unified-time-series-and-null-vintage-uniqueness.md) | Unified `time_series_point` with NULLS NOT DISTINCT vintage uniqueness | Accepted | 2026-06-03 | R1 |
| [0004](0004-structured-retention-shape.md) | Structured per-data-class retention; drop scalar `retention_ref` | Accepted | 2026-06-03 | R6 |
| [0005](0005-freshness-target-end-to-end.md) | Freshness target, end to end | Accepted | 2026-06-03 | R7 |
| [0006](0006-summary-table-and-content-hash-cache.md) | Dedicated `summary` table with content-hash cache | Accepted | 2026-06-03 | R4 |
| [0007](0007-embedding-backend-abstraction-and-fixed-dim.md) | Abstract the embedding backend behind one interface with a single fixed dimension per deployment | Accepted | 2026-06-03 | R8 |
| [0008](0008-rate-limiting-across-workers.md) | Enforce host-scoped rate limiting with a single fetch worker now and a Postgres token bucket later | Accepted | 2026-06-03 | R5 |
| [0009](0009-purge-erasure-vs-immutable-raw.md) | Carve out one audited purge path as the single exception to raw immutability, with a full referential cascade | Accepted | 2026-06-03 | R3 |
| [0010](0010-naming-atlascore.md) | Name the project AtlasCore (distribution `atlascore`, CLI `atlas`) and retire AtlasWire | Accepted | 2026-06-03 | — |
| [0011](0011-mcp-read-only-seven-tool-surface.md) | Expose a read-only seven-tool MCP surface over stdio with injection safety and side-effect confirmation instead of RBAC | Accepted | 2026-06-03 | — |
| [0012](0012-packaging-layout-src-atlascore.md) | Adopt a single `src/atlascore` import root; preserve `packages`/`apps` as the subpackage grouping | Accepted | 2026-06-03 | — (B1 packaging) |
| [0013](0013-source-registry-on-disk-and-slug-round-trip.md) | Source registry in `sources/`; YAML slug round-trips in `config`; B2 adds `source sync`/`list` | Accepted | 2026-06-03 | — (B2 registry) |
| [0014](0014-ssrf-validate-all-ips-pin-deferred.md) | SSRF guard validates all resolved IPs + re-validates redirects; connection-pinning deferred | Accepted | 2026-06-03 | — (B4 SSRF/§14) |
| [0015](0015-b5-parse-pipeline-and-nh3-sanitizer.md) | B5 parse pipeline: network-free parse, last-write-wins `document` upsert, nh3 HTML sanitizer | Accepted | 2026-06-04 | R2 (B5 parser/§2.4/§14) |
| [0016](0016-b6-fts-query-path-and-search-contract.md) | B6 full-text search: `'simple'`-config FTS over `document`, `websearch_to_tsquery` + `ts_rank`, static parameterized SQL, index-usage regression | Accepted | 2026-06-04 | — (B6 search/§2.4/§13/§15) |
| [0017](0017-b7-mcp-server-search-documents-and-audit-log.md) | B7 MCP server: `search_documents` (reuses B6 path, capped, sanitized, audited) + `audit_log` table (no FKs, sanitized `detail`) over stdio | Accepted | 2026-06-04 | R14 (B7 MCP/§2.11/§13/§14) |
| [0018](0018-b8-source-health-freshness-and-digest.md) | B8 freshness/health: `source_health` + pure status classifier, CLI-driven `refresh` (no scheduler yet), `get_source_health` (secret-free, audited), digest delivery seam | Accepted | 2026-06-04 | R7 (B8 health/§2.12/§2.5/§17) |
| [0019](0019-b9-time-series-fred-connector-and-macro-series.md) | B9 macro time-series: partitioned `time_series_point` (composite PK, NULLS NOT DISTINCT), FRED connector + `params`/credential resolve, access-method dispatch, `get_macro_series` | Accepted | 2026-06-04 | R1 (B9 macro/§2.7/§2.2/§14) |
| [0020](0020-b10-sec-nvd-document-connectors-and-output-kind-dispatch.md) | B10 SEC + NVD document connectors: `Connector.output_kind` dispatch (document vs timeseries), reuse the B5 document pipeline; SEC basic + NVD keyless | Accepted | 2026-06-04 | — (B10 connectors/§7.3/§7.4) |
| [0021](0021-b11-enrichment-entity-mention-embedding.md) | B11 enrichment: `entity`/`mention`/`embedding` (raw-SQL migration, excluded from drift-check); deterministic default backends (rule NER + hashing embeddings) with spaCy/sentence-transformers/cloud pluggable behind extras; idempotent re-enrich (R13); `get_entity_mentions` + search topic/entity filters | Accepted | 2026-06-04 | R8/R13 (B11 enrichment/§2.6/§2.8/§2.9/§2.3) |
| [0022](0022-b12-summaries-pipeline-and-content-hash-cache.md) | B12 summaries: `summary` table (raw-SQL `GIN(member_doc_ids)`, excluded by owning table) + R4 content-hash cache; deterministic extractive `local` backend with hosted LLM pluggable behind the `api` extra; `get_document_summary` computes-on-miss (gated by `LLM_BACKEND`/`allow_compute`) else null+reason | Accepted | 2026-06-04 | R4 (B12 summaries/§2.5/§2.4) |
| [0023](0023-b13-rule-based-alerts-delivery-and-get-alerts.md) | B13 alerts: `alert` table (normal/autogenerate) + four rules (sec_filing/cve_watch/macro_release + freshness) idempotent on `dedupe_key`; one delivery channel (webhook/ntfy/SMTP/log) that also backs `atlas digest`; read-only audited `get_alerts` | Accepted | 2026-06-04 | — (B13 alerts/§2.10/§2.6/§17) |
| [0024](0024-b14-purge-cascade-backups-and-restore-drill.md) | B14 erasure + backups: `atlas purge` full R3 cascade (incl. the multi-doc-summary regression) with a shared-raw guard (raw erased only on last reference) + retained audit; `atlas backup` (local pg_dump + raw copy, password via PGPASSWORD) + `infra/restore_drill.sh` | Accepted | 2026-06-05 | R3 (B14 purge/§16/§12) |
| [0025](0025-b15-hardening-and-mvp-completion.md) | B15 hardening + MVP completion: a `security` pytest marker (`make security`) consolidating SSRF/sanitizer/secrets-in-columns/prompt-injection + read-only MCP regressions; an end-to-end `smoke` demo (`make smoke`); README to as-built; **MVP B1–B15 complete + scope freeze** | Accepted | 2026-06-05 | — (B15/§19/§5) |
| [0026](0026-b15-polish-get-raw-document.md) | B15 polish: ship `get_raw_document` (the 7th MCP tool) — sanitized by default, gated/wrapped/byte-capped raw HTML behind `include_raw_html` with a distinct `mcp_raw_html_returned` audit; completes the full 7-tool MVP surface | Accepted | 2026-06-05 | R7 (§13/§2.7) |

> ADRs 0001–0011 encode the canonical-spec resolutions R1–R8 plus the naming and
> MCP-surface decisions: R1/R2 in 0003/0002, R3 in 0009, R4 in 0006, R5 in 0008,
> R6 in 0004, R7 in 0005, and R8 in 0007. **0012** records the B1 packaging-layout
> finalization (the `src/atlascore` import root); **0013** records the B2 source-registry
> location + slug round-trip + CLI surface.
