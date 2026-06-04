# 0022. B12 summaries: `summary` table + content-hash cache + extractive default backend

- **Status:** Accepted
- **Date:** 2026-06-04
- **Deciders:** Operator (single-operator project)
- **Spec reference:** AtlasCore Canonical Decisions Spec v1 (2026-06-03), docs/schemas §2.5 `summary` (R4), docs/mcp §2.4 + §4 `get_document_summary` pipeline, CLAUDE.md §20 (`SummaryBackend`/`LLM_BACKEND`), §16 (cloud egress, spend cap); MVP block **B12** (builds on the B5 document pipeline + B7 MCP/audit).

## Context

B12 adds **cached, cited** single- and multi-document summaries behind `get_document_summary`. The
forced decisions:

1. **Heavy/hosted LLM vs offline-CI + scope discipline.** CLAUDE.md §20 defines
   `LLM_BACKEND=local|api|none` where `api` is a hosted model (OpenAI/Google/Anthropic). A real LLM
   needs network/keys/spend and can't run in unit tests — same tension B11 resolved (ADR 0021).
2. **`summary` has a `GIN(member_doc_ids)` array index** that Alembic autogenerate can't round-trip
   (required so the R3 purge cascade can find multi-doc summaries by member).
3. **Does the MCP tool compute, or only read?** docs/mcp §1 says MCP "writes exactly one kind of
   row: audit_log", but §2.4/§4 explicitly describe `get_document_summary` computing on a cache miss
   (`allow_compute`, default true) and storing the summary row.
4. **Cache-only lookup keying.** With `LLM_BACKEND=none` there is no model identity to key the cache
   on, yet the tool must still return an existing cached summary for the content.

## Decision

- **Deterministic extractive `local` backend; hosted models pluggable behind the `api` extra.** MVP
  ships `enrichment.summaries.extractive.ExtractiveSummarizer` (lead-sentence extraction, per-claim
  citations to the **internal** document id, a word-count `token_cost`) as the `local` backend —
  deterministic, dependency-free, offline/CI-safe, and reproducible (which the R4 cache relies on).
  `get_summary_backend` returns `None` for `LLM_BACKEND=none` (cache-only), the extractive backend
  for `local`, and raises a clear `ConfigError` for `api` (pointing at the `api` extra). The
  `SummaryBackend` protocol (`model_name`/`model_version`/`prompt_version` + `summarize`) lets a
  hosted/local LLM slot in with no schema change. **This is the MVP realization of CLAUDE.md §20, not
  a contradiction** (mirrors ADR 0021's NER/embedding choice).
- **`summary` created via raw SQL in migration `0008`** (the `GIN(member_doc_ids)` index), excluded
  from the autogenerate drift-check **by owning table** in `migrations/env.py` (the established
  `0006`/`0007` pattern). The R4 cache key is `UNIQUE (source_hash, model_name, model_version,
  prompt_version)`; `source_hash` hashes the **ordered** member `content_hash`es. The ORM model maps
  columns for querying/inserts only.
- **The MCP tool computes on a cache miss when a backend is configured, and stores the row.** This
  follows the specific §2.4/§4 contract over the general §1 "audit-log-only" statement. The default
  `LLM_BACKEND=none` makes the tool **cache-only by default** (no writes beyond audit) — compute only
  happens once an operator configures a backend; with the MVP extractive `local` backend compute is
  offline and free. `allow_compute=false` forces cache-only for any backend. The spend-cap
  (`LLM_MONTHLY_SPEND_CAP_EUR`) degrade-to-cache-only path applies to the `api` backend and lands
  with it (deferred — local extraction has no spend).
- **Two-mode cache lookup.** Compute mode looks up the exact `(source_hash, model, version, prompt)`
  tuple (keyed to the active backend) and computes-then-stores on a miss. Cache-only mode
  (`backend=None` or `allow_compute=false`) looks up **any** cached summary for the `source_hash`
  (most recent), returning it if present, else `null` + a `reason`. Inserts are idempotent on the
  cache key (`ON CONFLICT DO NOTHING` + re-fetch), so a race never duplicates and an identical tuple
  is never re-summarized (R4).

## Consequences

- **Positive.** The done-when (a cached, cited summary via MCP; no re-summarization of an identical
  tuple) works end to end, offline, deterministically; staleness is automatic (a re-parsed member's
  new `content_hash` busts the cache); switching to a hosted/local LLM is a localized, documented
  change behind the existing extra; the `member_doc_ids` GIN is in place for the B14 purge cascade.
- **Negative / accepted (see STATUS Known issues).** The extractive summarizer is lead-sentence
  extraction, not abstraction — useful and honest but not a narrative summary until a real backend is
  wired; `api` backend + spend-cap enforcement + cloud-egress controls are deferred; `get_document_
  summary` writes a `summary` row on compute (a deliberate, documented exception to the
  "audit-log-only" posture, gated by `LLM_BACKEND`/`allow_compute`). None block the done-when.
