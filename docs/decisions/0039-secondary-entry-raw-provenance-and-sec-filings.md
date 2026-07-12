# 0039. Preserve and locally parse secondary entry payloads

- **Status:** Accepted
- **Date:** 2026-07-12

## Context

The SEC connector could fetch and parse one primary filing in a helper, but the production worker
pipeline only persisted the submissions JSON and therefore indexed metadata. Fetching from a parse
worker would violate the single-fetch-role and host-rate-limit invariants.

## Decision

Document connectors may return `FetchedEntryContent` from `fetch_entry_content`. The single fetch
role performs that guarded fetch and stores the bytes as a separate immutable `raw_document`; the
parse job carries a local parser reference and attribution. Parse workers only read the raw store.
Migration 0013 adds nullable `raw_document.origin_url` and `raw_document.parser_ref`. SEC opts in when
`extract_filings=true` and Tika is available, with metadata fallback on any secondary failure.

## Consequences

- Full SEC filing text follows the normal provenance, search, enrichment, purge, and audit paths.
- Rate limiting and SSRF enforcement remain centralized in the single fetch worker.
- Existing raw rows need no backfill; both columns are nullable and reversible.
