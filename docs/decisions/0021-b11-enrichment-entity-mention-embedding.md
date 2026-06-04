# 0021. B11 enrichment: entity/mention/embedding + deterministic default backends

- **Status:** Accepted
- **Date:** 2026-06-04
- **Deciders:** Operator (single-operator project)
- **Spec reference:** AtlasCore Canonical Decisions Spec v1 (2026-06-03), docs/schemas §2.6 `embedding` (R8/R13), §2.8 `entity`, §2.9 `mention`, docs/mcp §2.3 `get_entity_mentions`, CLAUDE.md §8/§20 (backends); MVP block **B11** (builds on the B5 document pipeline + B6/B7 search/MCP).

## Context

B11 enriches documents (language → NER → topics → embeddings) and adds the entity surface so
agents can ask "where has X been discussed lately?". The forced decisions:

1. **Heavy NLP dependencies vs offline/CI + scope discipline.** CLAUDE.md §8/§20 name **spaCy** (NER)
   and **sentence-transformers** (the `local` embedding backend). Both pull large models + downloads
   (torch, model artifacts) that would bloat the committed `uv.lock`, break the "no live network /
   no model download in unit tests" rule, and violate "don't add deps speculatively / build the
   smallest thing that works" (CLAUDE.md §2.5). They are already declared as **optional extras**
   (`local-embeddings`, `cloud`) — i.e. not installed by default.
2. **Three new tables with index types Alembic autogenerate can't round-trip** — the `entity`
   trigram `gin (canonical_name gin_trgm_ops)` + array GINs, and the `embedding` `pgvector
   vector(N)` column + HNSW ANN index.
3. **`embedding` dimension** must be fixed per deployment (R8) for the `vector(N)` ANN index.
4. **Idempotent re-enrichment** (R13 + "idempotent, safe to replay"): re-running enrichment must
   not duplicate mentions or re-embed unchanged content.

## Decision

- **Deterministic, dependency-free default backends; heavy backends stay pluggable.** B11 ships a
  rule-based NER (`enrichment.ner.rules.RuleBasedNer`: ticker/CIK patterns + capitalized-phrase
  extraction, precision-over-recall) and a hashing embedder (`enrichment.embeddings.hashing.
  HashingEmbeddingBackend`: BLAKE2b token bucketing, L2-normalized, **dim from `EMBEDDING_DIM`**).
  Both are deterministic (reproducible golden tests, R13 skip works) and need no model download, so
  the pipeline runs offline and in CI. The `NerBackend`/`EmbeddingBackend` interfaces + factories
  (`get_ner_backend`/`get_embedding_backend`) are defined so the spaCy small-model and
  sentence-transformers/cloud backends slot in later behind the existing extras with **no schema
  change** (same dim → no reindex; a different dim is the R8 backfill+reindex runbook). The
  `local` embedding backend is the hashing one in MVP; `openai`/`google` raise a clear `ConfigError`
  pointing at the `cloud` extra. **This is the MVP realization of CLAUDE.md §8/§20, not a
  contradiction of it** — the named libraries become drop-in backends when wanted.
- **`entity`/`mention`/`embedding` created via raw SQL in migration `0007`** (the established
  pattern from `document` GINs in 0003 and the partitioned `time_series_point` in 0006). The three
  tables — and every object owned by them — are excluded from the autogenerate drift-check by
  `_include_object` in `migrations/env.py`, matched **by owning table** (not a bare name prefix) so
  a normal column like `document.entity_ids` is still drift-checked. The ORM models map columns for
  querying/upserts only.
- **`embedding.vector` is `vector(384)`** — the default `EMBEDDING_DIM` (matches both the hashing
  backend and the future `all-MiniLM-L6-v2` swap). One active model+dim per deployment (R8).
- **Idempotency.** Re-enriching a document deletes that document's mentions from the current NER
  model+version and re-creates them (`mention` is unique on `(entity, document, model_name,
  model_version)`); `upsert_embeddings` is a no-op skip when the stored `source_hash`
  (`= document.content_hash`) is unchanged (R13). Entities are find-or-create (by identifier, then
  exact normalized name/alias) and are never deleted by enrichment — only the purge path removes
  them. The parse worker enqueues one idempotent `enrich` job per **changed** document
  (`dedupe_key = enrich:{document_id}:{content_hash}`), so the fetch→parse→enrich pipeline advances
  unattended.
- **`get_entity_mentions` MCP tool.** Resolves an entity by exact `entity_id`, then `identifier`
  (jsonb containment), then alias-aware **trigram fuzzy** `name` (`pg_trgm` `similarity`),
  following `merged_into` to the surviving id and returning a `match_confidence`. Mentions carry a
  sanitized `context_snippet` (untrusted evidence) + document/source provenance, capped at 100
  (default 50), audited. The deferred B7 `search_documents` `topics`/`entity_ids` array-overlap
  filters are now wired (the columns are populated by enrichment).

## Consequences

- **Positive.** The full §6 flow (fetch → parse → enrich → index) runs unattended with no heavy
  deps, offline, deterministically; the entity surface (done-when) works end to end; re-runs are
  idempotent; switching to spaCy/sentence-transformers/cloud is a localized, documented change.
- **Negative / accepted (see STATUS Known issues).** The rule-based NER is precision-over-recall
  (single-word generic proper nouns are dropped; "Bank of England" classifies as `company` via the
  `bank` suffix); the hashing embedder captures lexical overlap, not semantics, so ANN results are a
  weak baseline until a real backend is wired; language detection is the parser value + a tiny
  heuristic (else honest `None`); `sentiment` is always `null` (never fabricated); entity merging is
  resolvable (`merged_into`) but no merge operation is created in B11. None of these block the
  done-when, and each is a clean future upgrade.
