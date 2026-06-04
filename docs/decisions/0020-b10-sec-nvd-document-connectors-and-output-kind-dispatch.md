# 0020. B10 SEC + NVD: document-producing API connectors and `output_kind` dispatch

- **Status:** Accepted
- **Date:** 2026-06-04
- **Deciders:** Operator (single-operator project)
- **Spec reference:** AtlasCore Canonical Decisions Spec v1 (2026-06-03), docs/connectors §7.3 (SEC EDGAR) / §7.4 (NVD), §2.4 `document`, §14 (UA/limits/secrets); MVP block **B10** (builds on B5 document pipeline + B9 connector machinery).

## Context

B10 adds two more official/free API sources — SEC EDGAR filings and NVD CVEs — that must become
**searchable `document` rows**. The forced decisions:

1. **An `api` source can produce documents OR time-series.** B9 dispatched the single fetch worker
   on `access_method` (RSS → documents, API → time-series). That conflates SEC/NVD (API →
   documents) with FRED (API → time-series).
2. **How filings/CVEs become documents.** A dedicated SEC filing parser (full document extraction)
   and NVD-specific document model would be a lot of code for "basic" searchability.
3. **SEC/NVD access constraints.** SEC mandates a contact `User-Agent` + 10 req/s and needs no key;
   NVD's keyed tier sends the key as an `apiKey` **header** (the fetcher has no custom-header
   support yet).

## Decision

B10 implements `connectors.sec.submissions`, `connectors.nvd.cves`, and a dispatch refactor:

- **`Connector.output_kind` discriminator.** A `ClassVar[OutputKind]` (`document` | `timeseries`,
  default `document`). The single fetch worker (`_dispatch_fetch_job`) loads the claimed source's
  connector and dispatches on **`output_kind`**, not `access_method`: `timeseries` → `worker.macro`,
  else → the B5 document path. `build_document_records` guards on `output_kind == document`. RSS/SEC/
  NVD are `document`; FRED sets `timeseries`. This is the right discriminator because it reflects
  what the connector *produces*, independent of transport.
- **SEC + NVD reuse the B5 document pipeline.** Each connector implements `parse_entries`, turning a
  filing/CVE into a `FeedEntry` whose `content_html` is a short, searchable summary; the shared
  `ArticleParser` (trafilatura + `strip_html` fallback) produces the sanitized `document` body. No
  new filing/CVE parser — "basic" searchability falls out of the existing pipeline. `run_fetch_job`'s
  `FetchContext` now carries connector `params` + the resolved `credential` (RSS ignores them).
- **SEC submissions connector.** Fetches `/submissions/CIK##########.json` for the CIK in
  `params.cik` (zero-padded); each recent filing → `FeedEntry` (accession = GUID, the primary-doc
  URL on `www.sec.gov` = link, `form — company` = title, a `"<desc> (form <F>) filed by <co> on
  <date>"` body). The fetcher sends the required contact `User-Agent` (`DEFAULT_USER_AGENT`) and the
  source's 10 req/s limit; no key. Distinct accession numbers stay distinct documents.
- **NVD CVE connector.** Fetches the CVE 2.0 JSON API; each vulnerability → `FeedEntry` (CVE id =
  GUID, the NVD detail URL = link, the **English** description = body). Optional `params.keyword` /
  `results_per_page`. **B10 runs the keyless tier** (~5 req/30 s); the keyed `apiKey`-header tier is
  a documented follow-up (needs fetcher header support).
- **No schema change.** Filings and CVEs are `document` rows; B10 ships connectors + dispatch +
  parsers + two `sources/*.yaml`, no migration.

## Consequences

- **Positive:** SEC filings and NVD CVEs are searchable via `search_documents` (done-when met,
  verified). The `output_kind` discriminator cleanly separates document vs time-series connectors and
  is reusable by future connectors (GitHub, arXiv). Reusing the B5 pipeline means almost no new
  parsing code; secrets/UA/limits are handled by existing machinery.
- **Negative / costs:** SEC is **"basic"** — a per-filing metadata document (form/company/date +
  link), not the full filing text (full extraction needs sandboxed Tika, later). NVD is **keyless**
  (lower rate limit; the keyed header tier is deferred). Neither connector does conditional requests
  yet (ETag / `lastMod` cursor) — re-fetching is idempotent (dedupe by GUID + content hash) but
  re-reads the whole window. `params` values are stringly-typed (`results_per_page: "100"`).
- **Follow-ups / obligations:** sandboxed Tika full-filing extraction; NVD keyed tier (add optional
  custom headers to `SafeFetcher`, sanitized); conditional/incremental cursors (ETag, `lastMod`);
  entity/mention extraction for vendors/products (B11); these connectors power the `sec_filing` and
  `cve_watch` alerts (B13). Company Facts financial concepts as `time_series_point` (a separate SEC
  connector with `output_kind=timeseries`) if wanted.
- **Revisit triggers:** full filing-document analysis is needed; NVD rate limits bite (add the key);
  re-fetch cost matters (add conditional requests); a connector needs to produce BOTH documents and
  time-series (then `output_kind` becomes per-call, not per-connector).
