# 0032. B21 real embedding/LLM backends + spend-cap ledger + spaCy NER + reindex

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Operator (single-operator project)
- **Spec reference:** CLAUDE.md §20 (cost/perf, pluggable backends, hard spend cap), §16 (cloud egress), §8 (NER), R8 (one active dim); docs/mcp §4; post-MVP cycle B21. Builds on ADR 0021 (B11 embeddings/NER) + ADR 0022 (B12 summaries).

## Context

B11/B12 shipped the `EmbeddingBackend`/`SummaryBackend`/`NerBackend` protocols with deterministic
default implementations and `ConfigError` stubs for the real backends. B21 wires the real backends
**without** disturbing the offline CI path, plus the hard spend cap the spec mandates for cloud LLMs.

## Decision

- **Real backends behind the existing factories, default path unchanged.**
  - Embeddings: a new `sentence-transformers` backend value (real local embedder, `local-embeddings`
    extra, import-guarded) and `openai`/`google` cloud backends over **raw httpx** (so respx
    contract-tests them with no live network). `local` stays the deterministic hashing CI default.
  - Summaries: `LLM_BACKEND=api` → `HostedSummaryBackend` (OpenAI/Anthropic/Google over httpx,
    `llm_provider`); `local` stays the deterministic extractive summarizer.
  - NER: `NER_BACKEND=spacy` → `SpacyNer` (import-guarded) that **falls back to the rule-based
    extractor** with a logged warning if spaCy/the model is absent — enrichment never crashes.
- **Hard monthly spend cap via an `llm_spend` ledger** (migration `0012`, normal autogenerate table).
  Each paid call records one row (`period_ym`, tokens, `est_cost_eur` from a conservative
  operator-tunable price table). The summary pipeline checks month-to-date spend **before** a paid
  compute (`SummaryBackend.is_paid`) and, at/over `LLM_MONTHLY_SPEND_CAP_EUR`, **degrades to
  cache-only** (a cached row is still served; otherwise `found=False` + reason "LLM monthly spend cap
  reached; cache-only"). A successful paid compute records the ledger row in the **same transaction**
  as the summary, so the cap is cumulative and crash-consistent. Free backends never write the ledger.
- **Cloud-egress controls (§16/§20).** Cloud backends send no-train/zero-retention options where the
  provider supports it (`cloud_no_train`, e.g. OpenAI `store:false`), treat member bodies as untrusted
  evidence in a delimited block, validate every vector length == `EMBEDDING_DIM` (R8), and never log
  the key (header-only; `sanitize_for_storage` redacts it; provider errors carry only a status code).
- **`atlas reindex-embeddings`** (R8): a bounded, observable, per-batch-committed, idempotent
  (R13 no-op on unchanged `source_hash`) backfill/reindex for a model/dim switch — the concrete
  realization of the runbook's embedding-dim-switch step.

## Consequences

- **Positive.** AtlasCore can now run real local or cloud embeddings and hosted summaries, with the
  spec-mandated hard spend cap actually enforced and provable (the ledger). The deterministic
  hashing/extractive/rule-based path stays the offline CI default; all cloud/hosted code is
  respx-tested with no live network and import-guarded so missing extras never break import. A
  model/dim switch has a real, resumable command.
- **Negative / accepted.** The price table is an estimate (errs high so the cap trips early); operators
  tune it. Cloud embeddings still egress text and are **not** under the LLM spend cap (documented; an
  embedding-spend ledger is a possible follow-up). Hosted summaries cite member docs by inclusion (no
  fabricated claim-to-doc mapping). spaCy/sentence-transformers/vendor SDKs are optional extras — the
  cloud backends deliberately use raw httpx, so the `cloud`/`api` extras are only needed if an operator
  prefers the vendor SDKs elsewhere.
