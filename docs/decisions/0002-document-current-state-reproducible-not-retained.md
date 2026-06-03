# 0002. `document` holds current state; old parses are reproducible, not retained (R2)

- **Status:** Accepted
- **Date:** 2026-06-03
- **Deciders:** Operator (single-operator project)
- **Spec reference:** AtlasCore Canonical Decisions Spec v1 (2026-06-03), section 2.4, resolution R2

## Context

A core AtlasCore invariant is that **raw documents are immutable**: every fetched
payload is captured verbatim, gzipped, content-addressed on disk, and recorded in
`raw_document` before any parsing happens. Parsing then produces a canonical
`document` row.

Parsers improve over time (better boilerplate stripping, fixed extraction bugs,
new fields), so the same immutable `raw_document` can be re-parsed later under a
newer `parser_version`. This raises a design question for the normalized layer:
when we re-parse, do we **append a new versioned parsed-document row** (full
history of every parse) or do we **update the current `document` in place**?

Full parsed-version history is attractive for auditability but expensive and
mostly redundant: the authoritative evidence is already the immutable raw payload,
and any historical parse output can be regenerated deterministically by running a
pinned `parser_version` against the unchanged raw bytes. A versioned parsed table
would multiply storage and index size, complicate FTS/embedding maintenance
(which version do we index?), and add joins to every read path — all to store
output that is *reproducible by construction*. That conflicts directly with the
lean, single-box posture.

## Decision

**This is resolution R2.** The `document` table holds **current state only**, with
**last-write-wins** semantics on re-parse:

- Re-parsing an existing `raw_document` **UPDATES the matching `document` row in
  place**; it does not insert a new versioned row. There is **no versioned
  parsed-document table**.
- Old parsed versions are **not retained** but **are reproducible**: re-running the
  pinned `parser_version` against the immutable `raw_document` regenerates any
  prior output deterministically.
- `document.content_hash` is the hash **of the parsed body**. Comparing it before
  and after a re-parse **detects whether a re-parse actually changed output**
  (unchanged hash → no meaningful change; changed hash → downstream derivatives
  must be refreshed).
- `document.parser_version` records which parser produced the current row, so the
  exact prior output is always reconstructable from `raw_document` +
  `parser_version`.

The honest invariant chain therefore reads: **raw is immutable; normalized is
reproducible, not retained; there is no versioned parsed-document table.**

Relevant `document` columns supporting this decision (per spec 2.4):
`raw_document_id` (the raw it was parsed from), `content_hash` (of parsed body),
`parser_version`, `schema_version`, `canonical_url`, `parsed_at`. The
human/LLM-generated `summary` does **not** live on `document`; it lives in the
dedicated `summary` table (see ADR 0006), keyed by content hash so a changed
parse correctly invalidates derived summaries.

## Consequences

**Positive**

- Smaller, simpler normalized store: one current row per document, one FTS entry,
  one set of embeddings to maintain. Read paths stay join-light.
- The immutability claim stays **honest and minimal**: exactly one immutable layer
  (raw on disk + `raw_document`), and a clearly reproducible derived layer.
- Re-parsing is idempotent and observable: the parsed `content_hash` tells you
  whether anything actually changed, so embeddings/summaries are refreshed only
  when needed (supports the dedupe-before-embed/summarize cost rule).

**Negative / costs**

- We cannot show "what the parser produced last March" by querying a table; we
  must re-run that pinned `parser_version` against the raw payload to reproduce
  it. This requires keeping old parser versions runnable (pinned, tested
  fixtures) for as long as reproducibility matters.
- Last-write-wins means an in-place UPDATE; the prior parsed body is gone from the
  live row the moment a re-parse commits (the raw payload that produced it is not).

**Follow-ups / obligations**

- Parser code is version-pinned and golden-fixture tested (B5) so historical
  reproduction stays possible and deterministic.
- Re-parse logic must: read `raw_document`, parse under the current
  `parser_version`, compute the parsed `content_hash`, UPDATE the `document` row,
  and — only if the hash changed — re-index FTS and re-enqueue embedding/summary
  refresh.
- `docs/schemas/README.md` states the "reproducible, not retained" invariant
  beside the `document` table definition.

**Revisit triggers**

- A concrete, present need to diff or audit historical parser output without
  re-running parsers (e.g. a regulatory requirement) would justify a new ADR
  introducing a versioned parsed-output store — not before.
