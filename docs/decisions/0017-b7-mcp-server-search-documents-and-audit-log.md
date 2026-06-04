# 0017. B7 MCP server: `search_documents`, the `audit_log` table, and result caps

- **Status:** Accepted
- **Date:** 2026-06-04
- **Deciders:** Operator (single-operator project)
- **Spec reference:** AtlasCore Canonical Decisions Spec v1 (2026-06-03), §2.11 `audit_log` / R14, §13 (read-only MCP, injection safety, provenance), §14 (`sanitize_for_storage`), §15 (caps / untrusted evidence), docs/mcp §1–§2.1; MVP block **B7** (builds on B6 / ADR 0016).

## Context

B6 settled the FTS query contract (ADR 0016). B7 exposes it to agents over MCP — the *only*
agent-facing surface — and introduces the audit trail every tool call must write. The forced
decisions:

1. **`audit_log` shape and referential integrity.** The spec (§2.11) makes it append-only with a
   `retention_class` (R14: `purge` retained, `routine` rotates) and a sanitized `detail`. Its
   `source_id`/`target_ref` point at rows that purge later deletes — so enforced FKs would either
   block purge or cascade-delete the audit evidence.
2. **Where audit sanitization happens.** §14 forbids secrets in free-form columns/backups; the
   audit `detail` is free-form jsonb fed (partly) by agent-supplied tool parameters.
3. **How the MCP tool relates to B6.** `search_documents` must return full provenance (§1.3) and
   sanitized text only (§13), capped (§2.1: `limit ≤ 50`) — without duplicating the query path.
4. **Reconciling the illustrative spec sketch with what exists.** docs/mcp §2.1 sketches
   `source_ids[]`, `topics[]`, `entity_ids[]` filters and a `snippet_chars` knob — but enrichment
   (topics/entities) does not land until B11, and B6 filters by a single source slug.

## Decision

B7 implements `atlascore.storage.postgres.models.audit_log`, `atlascore.storage.audit`,
`atlascore.mcp_server` (FastMCP), migration `0004`, and `atlas mcp dev`:

- **`audit_log` has no foreign keys, by design.** `source_id` and `target_ref` are loose,
  non-referential pointers (`target_ref` is `text` precisely so a purged doc's audit entry — which
  records only hashes — still stands). This matches the spec DDL sketch (no FKs) and keeps the
  append-only log able to outlive purge of its referents. CHECK constraints bound `actor ∈
  {system,cli,mcp}` and `retention_class ∈ {routine,purge}`; indexes are plain btrees on `ts` and
  `action` (autogenerate-managed; no drift-check exclusions). `ts` defaults to DB time.
- **One sanitized write path.** Every audit write goes through `write_audit_log(...)`, which runs
  `detail` through `sanitize_for_storage` (the B3 helper) *before* it reaches the jsonb column — so
  a secret pasted into a tool parameter (e.g. a query string with `apikey=…` or a credentialed
  URL) is redacted at write time and never enters the audit trail or its backups.
- **`search_documents` reuses the B6 query path.** `SearchHit` and `_SEARCH_SQL` are extended with
  the provenance columns the tool needs (`source_id`, `language`, `topics`, `entity_ids`,
  `ingested_at`, `parser_version`) — additive, so `atlas search` is unaffected. The tool returns
  ranked hits with a sanitized `ts_headline` snippet + a provenance bundle (§1.3), **never raw
  HTML**, and writes one `audit_log(actor=mcp, tool_name=search_documents, action=mcp_tool_call,
  retention_class=routine)` per call. Input is validated by FastMCP/Pydantic (`query` 1–512 chars,
  `limit` 1–`SEARCH_LIMIT_MAX=50`); invalid input is rejected with `ToolError` before any DB work.
  The MCP cap (50) is stricter than the storage backstop (`MAX_LIMIT=100`).
- **Honest deviations from the illustrative sketch.** Source filtering is a single `source` slug
  (matching `atlas search`), not `source_ids[]`; the `topics`/`entity_ids` filters are deferred to
  B11 (the columns are surfaced in output, empty until enrichment runs); there is no `snippet_chars`
  knob (the `ts_headline` snippet is already short and bounded). A `truncated` flag uses a
  full-page heuristic (`len(hits) >= limit`) rather than a separate count query.
- **Transport + lifecycle.** The server runs over **stdio** (`atlas mcp dev`), carries
  server-level instructions framing all output as untrusted evidence, and creates its DB engine
  **lazily** on the first tool call so the process starts even before the database is reachable.
  The control plane never depends on MCP (§7): this server only *reads* processed stores and only
  *writes* `audit_log` rows.

## Consequences

- **Positive:** the first MCP tool is live and provably reachable over real stdio (a subprocess MCP
  client lists and calls it); every call is audited with secrets redacted at write time; provenance
  travels with every hit from the single B6 query path; result caps and input validation hold; the
  audit log survives purge of its referents. The audit infrastructure (`write_audit_log`,
  `Actor`/`RetentionClass`, the table) is ready for B8+ tools and the purge-retained entries (R3).
- **Negative / costs:** the implemented `search_documents` input is narrower than the spec sketch
  (single slug; no topic/entity filters yet) — a documented, forward-compatible gap, not a
  regression. `audit_log` has no DB-enforced referential integrity for `source_id`/`target_ref` (by
  design); the purge job (B14) reconciles these. The `truncated` heuristic can report
  `true` when exactly `limit` rows exist with none behind them.
- **Follow-ups / obligations:** B8 adds `get_source_health` (+ `source_health` + freshness job +
  daily digest) and is the next MCP tool — it must **not** be built before its table exists. The
  remaining tools (`get_macro_series` B9, `get_entity_mentions` B11, `get_document_summary` B12,
  `get_alerts` B13, `get_raw_document`) each reuse `write_audit_log` and the cap/provenance posture.
  `get_raw_document` is the only tool that may emit raw HTML, behind its explicit flag and a
  distinct audit action. When enrichment lands (B11), `search_documents` can gain topic/entity
  filters to converge on the spec sketch.
- **Revisit triggers:** topic/entity filtering is wanted (B11); structured-output schemas need
  tightening for a specific agent host; the server is ever exposed beyond stdio (then the §1.5
  SSRF gate and a transport auth story from docs/mcp apply).
