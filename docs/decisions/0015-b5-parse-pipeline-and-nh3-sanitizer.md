# 0015. B5 parse pipeline: network-free parse, last-write-wins document upsert, and an nh3 HTML sanitizer

- **Status:** Accepted
- **Date:** 2026-06-04
- **Deciders:** Operator (single-operator project)
- **Spec reference:** AtlasCore Canonical Decisions Spec v1 (2026-06-03), §2.4 `document` / R2, §14 (sanitization), docs/connectors §3.4–§3.5; MVP block **B5**.

## Context

B5 turns the per-GUID `parse` jobs the B4 fetch worker enqueues into canonical `document`
rows. Several concrete decisions were forced:

1. **Where parsing reads from.** The architecture says fetch is single-worker (rate-limited)
   while parse/enrich may scale out and do no source I/O. The parser must therefore reconstruct
   a document from the *immutable raw feed already on disk*, not by fetching the article URL.
2. **Document identity and re-parse.** Spec R2 makes `document` current-state, last-write-wins
   on re-parse, with `content_hash` of the parsed body as the change detector. The dedupe key is
   `canonical_url` (partial-unique). Null-URL items need a fallback identity.
3. **Index round-tripping.** The `document` table needs a partial-unique index, an FTS
   expression GIN (`to_tsvector('simple', …)`), and two array GINs. Alembic autogenerate cannot
   reflect/compare expression or partial indexes, which would break the CI drift-check.
4. **HTML sanitization at a security boundary.** Parsed bodies (and titles) are indexed,
   embedded, summarized, and shown to agents; fetched HTML is hostile (§14/§15). The B5
   adversarial review proved a hand-rolled `html.parser` + depth-counter sanitizer leaks active
   content on adversarial markup (an unterminated attribute quote in a `<script>` start tag is
   mis-tokenized as text; a stray close of one drop tag zeroes a shared counter held by another).

## Decision

B5 implements (`atlascore.parsers.article`, `atlascore.worker.parse`, `atlascore.storage.documents`,
`atlascore.security.sanitization`, migration `0003`):

- **Network-free parse.** The parse worker (`run_parse_job`/`run_one_parse`/`reparse_raw_document`)
  reads the immutable raw feed from the raw store, finds the entry by GUID via the source's
  connector (`FeedEntry.content_html`), and runs the `ArticleParser` (trafilatura primary +
  `strip_html` fallback; pinned `parser_version = "article@1.0.0"`). It holds **no** fetch
  advisory lock and may scale out (`SELECT … FOR UPDATE SKIP LOCKED`). `atlas parse <raw_id>`
  re-parses every item of one raw.
- **Last-write-wins upsert (R2).** `upsert_document` keys on `canonical_url` (partial-unique):
  re-parsing refreshes parser-owned fields in place and leaves enrichment-owned columns
  (`topics`/`entity_ids`/`cluster_id`) untouched. The `content_hash` fingerprint folds in the
  feed GUID so distinct null-URL items aren't collapsed; the remaining limitation (a changed
  null-URL body re-inserts rather than matches) is documented. The parse `dedupe_key` includes
  the raw `content_hash`, so a same-GUID in-place edit (new raw) re-enqueues a parse job instead
  of being stranded.
- **Migration index strategy.** Plain btree indexes are declared on the ORM model
  (autogenerate-managed); the partial-unique `uq_document_canonical_url`, the FTS `ix_document_fts`,
  and the array GINs `ix_document_topics`/`ix_document_entity_ids` are created via raw SQL in
  migration `0003` and excluded from autogenerate by `_include_object` in `migrations/env.py`
  (matched by exact, document-prefixed names). The CI drift-check stays empty.
- **nh3 for sanitization.** `strip_html` uses **nh3** (ammonia, a real HTML5 tokenizer) to remove
  dangerous tags and the contents of active-content elements correctly even for adversarial
  markup, keeping only structural tags so a stdlib second pass can derive block-aware text. Output
  is plain text (the strongest allowlist); a tag-preserving sanitizer is deferred until a surface
  needs sanitized HTML. The feed title is sanitized too.
- **Fetcher hardening (B4 follow-up).** The size cap is now enforced **incrementally on the
  streamed, decoded body** (`iter_bytes` with mid-stream abort) with a `Content-Length` early
  reject and a pre-stream MIME check, so a decompression bomb can't be fully materialized — closing
  a documented-control gap the review found. This refines, and does not change, ADR 0014's SSRF
  posture.

## Consequences

- **Positive:** parse scales out independently of the rate-limited fetch role; documents are
  reproducible from immutable raw (R2) and idempotent on replay/re-parse; the FTS index is ready
  for B6; the security boundary uses a battle-tested tokenizer instead of hand-rolled parsing
  (a class of leaks eliminated, proven by adversarial regressions); the fetcher resists
  decompression-bomb memory exhaustion.
- **Negative / costs:** a new runtime dependency (`nh3`, a small Rust/maturin wheel) on the
  security path; nh3 parses fragments in a body context, so bare text directly inside `<head>`
  can pass through as inert text (not active content, and not reachable via the feedparser-extracted
  RSS path); a feed edit re-parses every item of that feed (cheap, idempotent); the decoded-byte
  cap can overshoot by at most one decode chunk.
- **Follow-ups / obligations:** the tag-preserving allowlist sanitizer for `get_raw_document`
  (B7) if/when a surface returns sanitized HTML; the filing (B10) and macro (B9) document parsers
  for non-RSS access methods (`build_document_records` raises `NotImplementedError` until then);
  the purge cascade (B14) reconciles the non-referential array columns.
- **Revisit triggers:** an HTML/browser source is approved (full-page extraction via trafilatura,
  and the `<head>` edge becomes relevant); a surface needs to return sanitized HTML rather than
  text; the fetch role scales beyond one worker (per-host token-bucket path).
