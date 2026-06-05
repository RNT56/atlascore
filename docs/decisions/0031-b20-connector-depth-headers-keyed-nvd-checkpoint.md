# 0031. B20 connector depth: custom headers + NVD keyed tier + incremental checkpoint

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Operator (single-operator project)
- **Spec reference:** CLAUDE.md §10 (connector rules, ETag/incremental), §14 (secrets never in columns), §17 (idempotent replay); docs/schemas §2.1; post-MVP cycle B20. (Tika/full-filing and spaCy NER from the original area plan are deferred to B22/B21.)

## Context

The B10 connectors fetched the full recent window every tick and the NVD connector ran keyless only.
Three depth upgrades, each keeping the default path offline/CI-safe:

1. **Custom request headers** (foundation) — connectors need to send an auth header without weakening
   SSRF/redirect/size handling or letting a poisoned param strip the project UA.
2. **NVD keyed tier** — NIST's higher-rate tier needs the API key as an `apiKey` *header*, and the
   key must never be logged or stored.
3. **Incremental fetch** — avoid re-fetching/re-enqueuing the whole window each tick; needs a
   persistent, per-source, runtime-owned cursor.

## Decision

- **`SafeFetcher.fetch(..., extra_headers=)`** merges caller headers via **`setdefault`** — they can
  never override the managed `User-Agent`/`If-None-Match`/`If-Modified-Since`, and are re-applied on
  every redirect hop. SSRF/redirect/MIME/size logic is untouched. `FetchContext.extra_headers` is
  not used directly; connectors pass headers at the `fetch` call.
- **NVD keyed mode:** when `ctx.credential` is set (resolved from `credential_ref` → `NVD_API_KEY`),
  `NvdCveConnector` sends it as the `apiKey` header. The new `sources/nvd_recent_cves_keyed.yaml`
  (disabled by default, `rate_limit 50/30s`) opts in. The key is never logged/stored: it rides only
  in a request header, and `sanitize_for_storage` redacts both the dict form (`{'apiKey': …}`, via
  `_CRED_KEY_FRAGMENT`) and the header-line form (`_HEADER_RE` extended with `apikey`/`x-api-key`).
- **Incremental cursor in a dedicated `source.checkpoint` jsonb** (migration 0011, nullable) — NOT
  `source.config` (the lossless YAML round-trip home, overwritten on every `atlas source sync`), NOT
  `crawl_job.payload` (cursors must persist across jobs), NOT `raw_document` (source-scoped, not
  blob-scoped). `checkpoint` is **runtime-owned**: written only by the worker after a successful
  fetch, never by the YAML loader. Helpers `storage.checkpoints.get/set_checkpoint` (merge-update).
- **Connector cursor hooks** (default no-ops, so RSS/FRED are unaffected): `Connector.next_checkpoint`
  (compute the cursor to persist) and `Connector.filter_entries` (skip already-seen items — an
  optimisation; the parse-job dedupe stays the correctness gate). NVD windows server-side
  (`lastModStartDate`+`lastModEndDate` from the cursor + `FetchContext.now`); SEC records the newest
  `filingDate`/accession and filters strictly-older filings (keeping same-day, so nothing unseen is
  missed). The worker injects the checkpoint into `FetchContext` before fetch and persists
  `next_checkpoint` in the same job transaction (cursor only advances on a fully-processed fetch).

## Consequences

- **Positive.** Connectors can authenticate via headers without weakening SSRF; the NVD keyed tier
  unlocks ~10× throughput with the key fully redacted from logs/columns/backups; SEC + NVD fetch
  incrementally, cutting redundant work each tick while staying idempotent (re-fetch is still safe
  via GUID+hash dedupe). No partitioned/GIN DDL — migration 0011 is a plain drift-checked column.
- **Negative / accepted.** The cursor is best-effort (a connector returning no `next_checkpoint`
  simply keeps full fetches). SEC's `recent` array is always re-fetched (the API has no date param);
  the cursor only trims how many stale parse jobs are re-enqueued. `FetchContext` gained `now` +
  `checkpoint` fields (generic, defaulted). Tika full-filing extraction and spaCy NER (bundled in the
  original area plan) are intentionally deferred to B22 and B21.
