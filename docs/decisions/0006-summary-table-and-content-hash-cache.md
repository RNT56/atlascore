# 0006. Dedicated `summary` table with content-hash cache (R4)

- **Status:** Accepted
- **Date:** 2026-06-03
- **Deciders:** Operator (single-operator project)
- **Spec reference:** AtlasCore Canonical Decisions Spec v1 (2026-06-03), sections 2.4, 2.5, 6, 8 (B12), 9, resolutions R3, R4; corrections (4), (5)

## Context

AtlasCore can produce LLM summaries of one document or of several documents
together (e.g. a clustered topic). Three forces shape where and how summaries are
stored:

- **Provenance.** Every summary must record which model and prompt produced it,
  with citations back to internal document IDs — summaries are derived claims and
  must be auditable evidence, never free-floating text.
- **Cost.** Hosted LLMs cost money per call; re-summarizing identical content
  wastes spend. The cost rules require dedupe-before-summarize, a content-hash
  cache, and a hard monthly spend cap when a cloud backend is used.
- **Erasure (R3).** The audited `atlas purge` path must be able to delete personal
  data everywhere it landed, including inside summary text and citations —
  including **multi-document** summaries that link to a purged doc only through an
  array column.

Putting a `summary` field on `document` (the obvious shortcut) fails all three: it
cannot represent multi-doc summaries, has nowhere for model/prompt provenance, and
gives no cache key. Hence summary is explicitly **not** a `document` column
(spec 2.4 note).

## Decision

**This is resolution R4.** Summaries live in a **dedicated `summary` table**
(spec 2.5) with a content-hash cache key and full provenance:

**Columns:** `id`, `scope (single | multi)`, `document_id (fk, null for multi)`,
`member_doc_ids text[]` (ordered; `single = [doc]`, `multi = [...]`),
`source_hash` (a hash over the ordered member `content_hash`es), `model_name`,
`model_version`, `prompt_version`, `summary_text`, `citations jsonb` (internal doc
IDs → claims), `token_cost`, `created_at`, `schema_version`.

**Cache key / uniqueness:**
`UNIQUE (source_hash, model_name, model_version, prompt_version)`.

**Cache semantics:**

- `source_hash` is computed over the **ordered member document `content_hash`es**.
  If any member's parsed `content_hash` changes (e.g. a re-parse under ADR 0002),
  `source_hash` changes → **cache miss → recompute**.
- The same `(content, model, prompt)` tuple is **never re-summarized**: identical
  `source_hash` + model + version + prompt → cache hit, return stored row.
- **Dedupe before summarize**: cluster/dedup documents first, then summarize, so
  syndicated duplicates are not summarized repeatedly.

**Backends (section 6).** `LLM_BACKEND = local | api | none`. With `none`,
`get_document_summary` returns **only cached summaries**, else `null` with a
reason. Input documents are treated as **untrusted evidence, never instructions**.
A **hard monthly spend cap** (`LLM_MONTHLY_SPEND_CAP_EUR`) applies when `api` is
used.

**Indexes:** `UNIQUE (source_hash, model_name, model_version, prompt_version)`;
`btree (document_id)`; and — per correction (4) — **`GIN (member_doc_ids)`** so
the purge job can find multi-doc summaries by member.

**Purge cascade for summaries (R3 + correction 4).** When purging `doc_id`, the
cascade must delete **every summary where `document_id = doc_id` OR
`doc_id = ANY(member_doc_ids)`**. A `document_id`-keyed delete alone would miss
multi-scope summaries (which set `document_id = null` and link only via
`member_doc_ids[]`), leaving purged personal data alive in `summary_text` /
`citations` and breaking the audited-erasure guarantee. Affected multi-doc
summaries are recomputed or hard-deleted. A **purge regression test covering a
multi-doc summary** is required (B14).

**MCP surface.** This table powers `get_document_summary`, which returns a cached,
cited summary of one or more docs, is `LLM_BACKEND`-aware, and returns
`null` + reason when no summary exists and none can be produced.

**Compliance / cloud egress (correction 5).** When `LLM_BACKEND = api` (or cloud
embeddings), document text — which inherently contains personal data (names,
quotes) — **egresses to a third-party (likely third-country) processor**. AtlasCore
does **not** describe cloud LLM/embedding use as consistent with a pure
internal-only / household-exemption posture. Either use **local backends for
PII-bearing docs** as the required posture, or document the processor reality and
configure **no-training / zero-retention / DPA** settings with the vendor. This is
stated here and in `docs/compliance/README.md`.

## Consequences

**Positive**

- Summaries carry full provenance (model, version, prompt, citations) and are
  auditable evidence.
- The content-hash cache eliminates re-summarizing identical tuples, directly
  bounding LLM spend; the spend cap is a hard backstop.
- Multi-document summaries are first-class, and the `GIN (member_doc_ids)` index
  + OR-clause cascade keep erasure honest for both single and multi scope.
- `get_document_summary` degrades gracefully (`none` backend → cache-only or
  `null` + reason).

**Negative / costs**

- A summary referencing many documents may be hard-deleted on purge rather than
  partially edited; recompute cost is accepted to keep erasure simple and
  provable.
- Cloud backends introduce a real data-egress/compliance obligation that must be
  documented and configured, not glossed over.

**Follow-ups / obligations**

- B12 implements the `SummaryBackend`, the `summary` table, the content-hash
  cache, and `get_document_summary`.
- The R3 purge job (B14) includes the `member_doc_ids[]` OR-clause and the
  multi-doc-summary regression test; `GIN (member_doc_ids)` exists in the
  migration.
- `docs/compliance/README.md` and `docs/security/README.md` record the cloud-egress
  posture and untrusted-evidence handling; summaries cite evidence IDs and never
  invent facts.

**Revisit triggers**

- Publishing/sharing summaries externally (the GDPR personal/household exemption
  lapses) → revisit compliance and cloud-egress posture (new ADR / compliance
  update).
- A second active LLM model/prompt in production → the cache key already
  distinguishes them; no schema change needed, but spend-cap accounting must cover
  both.
