# 0007. Abstract the embedding backend behind one interface with a single fixed dimension per deployment

- **Status:** Accepted
- **Date:** 2026-06-03
- **Deciders:** Operator (single-operator project)
- **Spec reference:** AtlasCore Canonical Decisions Spec v1 (date 2026-06-03), resolution R8; sections 2.6 (`embedding`), 6 (embedding & LLM backends), 7 (env vars), 10 (compliance); MVP block B11.

> ADRs are immutable once Accepted. To change this decision, write a new ADR and
> mark this one *Superseded by*. Where this file and the canonical spec disagree,
> the spec wins and this ADR must be corrected.

## Context

AtlasCore enriches canonical `document` rows with vector embeddings so that
semantic search and clustering can run on one box. Two forces collide:

1. **Hardware reality.** The operator currently lacks a powerful machine.
   Running a local sentence-transformers model is possible but slow; cloud
   embedding APIs (OpenAI `text-embedding-3-*`, Google `text-embedding-004`)
   are faster and cheap at personal scale. Both must be first-class so the
   operator can move between offline/privacy and hosted/throughput postures
   without rewriting the pipeline.

2. **pgvector's fixed-dimension constraint.** The `embedding.vector` column is
   `vector(N)` and any ANN index (HNSW preferred) is built for exactly one
   dimension `N`. Different models emit different dimensions (e.g. MiniLM 384,
   `text-embedding-3-small` 1536, `text-embedding-3-large` 3072). A single
   column and a single ANN index therefore cannot hold mixed dimensions at the
   same time. Switching the active model is not an online toggle: it changes the
   geometry of the whole vector space, so every stored vector becomes
   incomparable to new ones.

The pipeline must also stay idempotent and safe to replay (B11): re-running
enrichment over an unchanged document must not insert duplicate chunk rows.

A further force, surfaced during review, is **data egress**: full article body
text — which the compliance posture (section 10) admits contains personal data
(names, quotes) — is sent verbatim to a third-party (and likely third-country)
processor when a cloud backend is selected.

## Decision

This ADR encodes **resolution R8**.

**One pluggable `EmbeddingBackend` interface** lives in
`packages/enrichment/embeddings`. Implementations:

| Backend | `EMBEDDING_BACKEND` | Example model | Typical dim |
| --- | --- | --- | --- |
| Local | `local` | sentence-transformers (e.g. `all-MiniLM-L6-v2`) | 384 |
| OpenAI | `openai` | `text-embedding-3-small` / `-large` | 1536 / 3072 |
| Google | `google` | `text-embedding-004` / gecko | 768 |
| Test stub | (test only) | deterministic hash-based vectors | configurable |

The active backend, model, and dimension are set by environment variables
(section 7), never hardcoded:

```
EMBEDDING_BACKEND   = local | openai | google
EMBEDDING_MODEL     = <model name>
EMBEDDING_DIM       = <fixed integer N>
EMBEDDING_API_KEY   = <only when cloud>
```

**Default for development is `local`.** Cloud is the supported production path
for this operator because of the hardware reality; the interface makes that a
one-line config change, not a code change.

**Exactly one active embedding model + dimension per deployment.** The
`embedding.vector` column is `vector(N)` for the single configured `N`, and the
HNSW ANN index is built for that `N`. Every persisted row records its
provenance: `model_name`, `model_version`, and `dim`. The `dim` and
`model_version` columns exist so a mismatch (a row embedded under a different
model) is detectable rather than silently compared against incompatible
geometry.

**Idempotency / replay (R8 + B11 invariant).** `embedding` carries a uniqueness
key `unique(document_id, chunk_index, model_name, model_version)` and a
`source_hash`. Re-running enrichment over a chunk whose `source_hash` is
unchanged is a **no-op (skip)**, mirroring the `summary` cache rule (R4). This
keeps "idempotent, safe to replay" honest and prevents duplicate chunk rows.

**Switching backend or dimension is a bounded, documented BACKFILL + reindex,
not an online change.** The procedure (a runbook under `docs/runbooks/`) is:

1. Set the new `EMBEDDING_BACKEND` / `EMBEDDING_MODEL` / `EMBEDDING_DIM`.
2. Create the new `vector(N')` column / table generation and a fresh HNSW index.
3. Re-embed all documents under the new model (resumable, observable, bounded;
   uses the same `crawl_job` enrich path so it survives restarts).
4. Cut search over to the new column once backfill completes.
5. Drop the old vectors and index.

**Compliance coupling (review correction 5).** Because cloud embedding sends
PII-bearing body text to an external processor, the compliance docs and section 6
must NOT describe cloud embedding as automatically consistent with a pure
internal-only / household-exemption posture. The required posture:

- For documents known to carry sensitive personal data, **local backends are the
  preferred posture**; cloud egress is an explicit operator choice.
- When a cloud backend is used, the operator MUST configure the processor for
  **no-training, zero/limited-retention, and a DPA where available**, and
  document the processor + region. See ADR 0009's purge cascade and
  `docs/compliance/README.md`.

## Consequences

- **Positive:**
  - The pipeline is hardware-agnostic: offline/private (`local`) or
    hosted/throughput (`openai`/`google`) is a config flip, no code change.
  - A single `vector(N)` column and one HNSW index keep the storage layer lean
    and fast — no per-model column sprawl, consistent with single-box discipline.
  - Provenance (`model_name`, `model_version`, `dim`) makes incompatible vectors
    detectable instead of silently wrong.
  - The uniqueness key makes B11 enrichment replay-safe with no duplicate chunks.

- **Negative / costs:**
  - Only one embedding space is queryable at a time; you cannot A/B two models
    online without the backfill procedure.
  - Changing models is operationally heavy (full re-embed + reindex).
  - Cloud backends introduce a third-party/third-country data-egress surface that
    must be governed (DPA, no-training, retention settings) and documented.

- **Follow-ups / obligations:**
  - Write the `EmbeddingBackend` interface + four implementations (incl. test
    stub) in `packages/enrichment/embeddings` (B11).
  - Add the `unique(document_id, chunk_index, model_name, model_version)`
    constraint and `source_hash` skip logic via Alembic migration (B11).
  - Write the **backfill + reindex runbook** under `docs/runbooks/`.
  - Update `docs/compliance/README.md` and section 6 with the cloud-egress note
    and the local-preferred-for-PII posture.
  - Record `model_name`/`model_version`/`dim` on every `embedding` row.

- **Revisit triggers:**
  - Wanting two embedding models live simultaneously (would need per-model
    columns/tables/indexes — a new ADR).
  - Outgrowing pgvector for ANN at this scale (revisit the vector store choice).
  - Publishing/sharing results externally (the household exemption lapses;
    re-evaluate cloud egress of PII-bearing text — see ADR 0009 and section 10).
