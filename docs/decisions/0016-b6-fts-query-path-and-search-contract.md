# 0016. B6 full-text search: the `document` FTS query contract

- **Status:** Accepted
- **Date:** 2026-06-04
- **Deciders:** Operator (single-operator project)
- **Spec reference:** AtlasCore Canonical Decisions Spec v1 (2026-06-03), §2.4 `document` (FTS GIN index), §13 (MCP read posture / provenance), §15 (cap result sizes); MVP block **B6**.

## Context

B5 shipped the `document` table and its `ix_document_fts` GIN index
(`gin(to_tsvector('simple', coalesce(body_text, '')))`, migration `0003`). B6 builds the
**query path** on top of it: a search over `document` with date/source filters and a result cap,
exposed as `atlas search`. Several decisions were forced, and they form a contract that **B7's
MCP `search_documents` tool will reuse directly** — so they are recorded here rather than left
implicit:

1. **Which text-search config.** The index baked in `'simple'` (no stemming, no stopword
   removal). The query must use the *identical* config on both the indexed `tsvector` and the
   `tsquery`, or term matching diverges and — worse — the planner silently stops using the index.
2. **How to parse free-text input.** `to_tsquery` raises a syntax error on ordinary user input
   (e.g. two bare words, a stray operator). For an interactive CLI / agent tool that is
   unacceptable.
3. **How to guarantee the index is actually used,** and how to keep a regression from silently
   reintroducing a full-table scan.
4. **Injection surface.** Optional filters (source slug, date bounds) make the `WHERE` clause
   dynamic; naive string-building would put a SQL-injection hole on a security-sensitive path.

## Decision

B6 implements `atlascore.storage.search.search_documents` + the `atlas search` CLI:

- **`'simple'` config in lockstep with the index.** The query's tsvector expression is
  byte-identical to the index expression (`to_tsvector('simple', coalesce(body_text, ''))`), so
  the `ix_document_fts` GIN index is used. A module comment marks `'simple'` as load-bearing:
  changing it requires reindexing.
- **`websearch_to_tsquery('simple', :q)`** parses the user query — it never raises on arbitrary
  input and supports `"quoted phrases"`, `or`, and `-term` exclusion. Ranking is `ts_rank`
  (descending), tie-broken by `published_at DESC NULLS LAST`. Each hit carries a one-fragment
  `ts_headline` snippet with matches wrapped in `«…»` (Postgres mishandles empty `StartSel`/
  `StopSel`, so non-empty markers are required) plus provenance: source slug + name, canonical
  URL, published time, document id.
- **Static SQL, fully parameterized.** The statement has no string interpolation; the optional
  filters are always-present `CAST(:p AS …) IS NULL OR <col> = :p` guards, so every caller value
  — query, slug, date bounds, limit — is a bound parameter. No injection surface; the FTS `@@`
  predicate still drives the index (the guards are residual filters).
- **Result cap.** `limit` is clamped to `[1, MAX_LIMIT=100]` in the storage layer (CLI default
  20, `--limit` clamps), bounding CLI and (future) MCP payloads (§15).
- **Index-usage regression.** An integration test runs `EXPLAIN` with `enable_seqscan = off` and
  asserts `ix_document_fts` appears in the plan — which only happens when the query's tsvector
  expression matches the index expression. This fails loudly if a future edit drifts the config
  or the `coalesce` shape.
- **Sanitized text only.** Only `body_text` (already sanitized in B5) is searched and returned;
  raw HTML is never exposed by this path (§13). That stays true for the B7 MCP tool.

## Consequences

- **Positive:** search is index-backed and provably so (regression-guarded); robust to arbitrary
  user input; no injection surface on a security-sensitive path; provenance travels with every
  hit; the contract (config lockstep, `websearch_to_tsquery`, `ts_rank`, snippet, cap, sanitized
  text) is settled for B7's MCP `search_documents` to reuse without re-litigating it.
- **Negative / costs:** `'simple'` means no stemming — “rate” does not match “rates”, and there
  is no language-aware normalization. Accepted for MVP (predictable, language-agnostic over mixed
  feeds); a stemmed config would be a new index + a documented reindex, not an online change.
  `ts_rank` is frequency/proximity-based, not BM25; fine at single-operator scale. The `«…»`
  snippet markers are shown literally by the CLI (no ANSI), which is plain but unambiguous.
- **Follow-ups / obligations:** B7 wraps this query as the MCP `search_documents` tool (strict
  schema, audit log, the same result cap and sanitized-text guarantee). If FTS is ever outgrown,
  Meilisearch is the documented single-binary escape hatch (CLAUDE.md §8), not OpenSearch.
- **Revisit triggers:** stemming/relevance demands outgrow `'simple'` + `ts_rank` (move to a
  language config or Meilisearch, with a reindex); a non-English corpus needs per-language
  configs; result volumes need keyset pagination beyond a single capped page.
