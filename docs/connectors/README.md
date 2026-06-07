# AtlasCore — Connectors & Source Registry

> **Scope:** This document is the deep dive for the connector SDK, the connector
> lifecycle, the canonical source-registry YAML, the policy / default-deny model,
> per-connector operational notes (RSS, FRED, SEC EDGAR, NVD, GitHub, arXiv), the
> fixtures policy, parser golden-output tests, and the add-a-source workflow.
>
> **Authority:** The *AtlasCore Canonical Decisions Spec v1* (2026-06-03) is the
> single source of truth. Where this file overlaps the spec, the spec wins. Table
> names, column names, the source-YAML shape, env vars, the MVP block ordering
> (B1–B15), and the 7-tool MCP surface here match the spec exactly.
>
> **Date:** 2026-06-03

---

## 1. Mental model

A **connector** is the only code allowed to talk to an external source. Everything
downstream (parsers, enrichment, storage, MCP) consumes the connector's structured
output and never re-fetches. Connectors are:

- **Governed** — a source is only fetched when its `policy_status` is `approved`
  (or `restricted` with encoded constraints). Default-deny everywhere else.
- **Provenance-first** — the immutable raw payload is stored *before* parsing, and
  every output record carries `source_id`, `raw_document_id`, `parser_version`,
  `schema_version`, and source attribution (source, URL, author, timestamp).
- **Idempotent** — replaying a job never duplicates rows. Dedupe is by
  `content_hash` and by source-native keys (GUID / canonical URL / external id).
- **Unattended** — backoff with jitter, `Retry-After` respect, conditional
  requests (ETag / Last-Modified / cursor), and circuit-breaking on repeated
  failure. Health is reported through `source_health` and the daily digest.

A connector is a Python class implementing the lifecycle in §3, registered against
a source-registry row (§4) that is itself the mirror of a git-tracked YAML file.

**Architecture rule (from the spec):** the ingestion/control plane never depends on
MCP. Connectors are driven by the scheduler → `crawl_job` queue → worker loop, and
by the `atlas` CLI. Agents never trigger crawls and never change source policy.

---

## 2. Where connectors live

```text
packages/
  connectors_sdk/        # connector interface (ABC), result types, test harness
  connectors/
    rss/                 # generic RSS/Atom connector
    fred/                # FRED macro time-series connector
    sec/                 # SEC EDGAR (companyfacts, submissions, filings index)
    nvd/                 # NVD CVE connector
    github/              # GitHub releases / advisories connector
    arxiv/               # arXiv Atom API connector
  parsers/
    article/             # HTML article -> document (trafilatura + fallback)
    filing/              # SEC filing -> document
    macro/               # macro series payload -> time_series_point rows
  security/
    url_safety/          # SSRF guard (§9) used by the fetcher for every request
    sanitization/        # HTML sanitizer used before indexing / MCP exposure
    secrets/             # credential resolution + sanitize_for_storage helper
```

> **Packaging note (finalized in B1 — [ADR 0012](../decisions/0012-packaging-layout-src-atlascore.md)):**
> the import root is a single **`src/atlascore`**. Read every `packages/<x>/…` path in this
> doc as `src/atlascore/<x>/…` (e.g. the `packages/connectors/rss` shown above is
> `src/atlascore/connectors/rss`); the dotted import stays `atlascore.connectors.rss…`,
> exactly the YAML `fetch.connector` convention. The git-tracked registry lives in
> `sources/` ([ADR 0013](../decisions/0013-source-registry-on-disk-and-slug-round-trip.md)).

---

## 3. Connector lifecycle

Every connector implements this lifecycle. `discover()` is optional; the rest are
required. The worker drives these methods in order per `crawl_job`; each stage is
small, observable, and re-runnable.

```text
discover()  (optional) -> fetch() -> validate() -> parse() -> normalize()
   -> checkpoint() -> healthcheck()
                                policy gate runs BEFORE fetch()
```

### 3.0 policy gate (pre-fetch, not a method)

Before any `fetch()` runs, the worker checks the source's `policy_status`. Only
`approved` and `restricted` (with its encoded constraints satisfied) proceed.
`pending`, `unknown`, `prohibited`, and missing policy are **refused** with an
`audit_log` entry — the job ends `failed` (not retried). Local fixtures are the only
exception (offline tests). See §5.

### 3.1 `discover()` — optional

Enumerate what is fetchable without downloading bodies: a feed of feed URLs, a list
of CIKs, a category index, a paginated id cursor. Emits *child* `crawl_job` rows
(`job_type=fetch`) with deterministic `dedupe_key`s. Connectors with a single static
endpoint (e.g. one RSS feed) skip this.

### 3.2 `fetch()` — network I/O only

- Issues the HTTP request via the shared `httpx` client **through the SSRF guard**
  (§9). No connector ever opens its own socket or bypasses URL safety.
- Sends conditional headers from the last checkpoint: `If-None-Match` (ETag),
  `If-Modified-Since` (Last-Modified), or a provider cursor in the query.
- Sends the transparent `DEFAULT_USER_AGENT` (project UA + contact); some providers
  require it (SEC EDGAR — §7.3).
- Obeys the **per-host rate limit** via the in-process per-host token bucket. MVP =
  exactly one fetch worker (fetch concurrency per host = 1); see §5.
- On `304 Not Modified`: no new raw document; the job is `done`, checkpoint
  refreshed, `source_health.last_attempt_at`/`last_success_at` updated.
- On success: returns the raw bytes + response metadata (status, headers, final
  URL after redirects). The worker writes the **immutable raw document to disk**
  (gzipped, content-addressed) and inserts the `raw_document` row *before* parse.

### 3.3 `validate()` — gatekeeping the payload

Reject before persisting/parsing when any of these fail:

- HTTP status is not an accepted success (2xx for new content, 304 handled above).
- MIME type is not in the connector's allowlist (e.g. `application/atom+xml`,
  `application/json`, `application/xml`, `text/html`).
- Size exceeds the connector's cap (guards against decompression bombs / runaway
  bodies).
- The final resolved URL still passes SSRF re-validation (every redirect hop is
  re-checked — §9).

Validation failures are transient vs permanent: malformed-but-served content fails
the *parse* stage (job may move to `dead` after `max_attempts`); a blocked URL or
disallowed scheme fails *validation* and is not retried.

### 3.4 `parse()` — bytes -> structured items

Pure, deterministic, **no network**. Turns the raw payload into one or more
structured items. Carries `parser_version`. Parsing reads only from the immutable
raw document, so it is reproducible: re-running the pinned `parser_version` against
the same `raw_document` reproduces the output (spec R2). Source-specific quirks live
here, never in storage.

### 3.5 `normalize()` — items -> canonical records

Maps parsed items onto canonical records (`document`, `time_series_point`,
`entity`/`mention`, etc.) with:

- Attribution (source, canonical URL, author, published timestamp).
- UTC `timestamptz`; preserve source-local time in `source_local_published_at`.
- `content_hash` of the parsed body (drives dedupe and re-parse detection).
- `schema_version`.
- Deterministic dedupe key — `unique(canonical_url)` for documents (where present),
  `UNIQUE NULLS NOT DISTINCT (provider, series_id, ts, vintage)` for time-series,
  source-native GUID/external-id otherwise. Upserts make replays no-ops.

Multi-table canonical writes happen in **one transaction**.

### 3.6 `checkpoint()` — durable progress

Persists what's needed to resume incrementally without re-fetching: the response
`ETag` and `Last-Modified` on the `raw_document`, plus any provider cursor /
high-water mark. Idempotent: crash-and-restart resumes from the last checkpoint.

**As-built (B20, [ADR 0031](../decisions/0031-b20-connector-depth-headers-keyed-nvd-checkpoint.md)):**
the provider cursor lives in the dedicated **`source.checkpoint` jsonb** column (runtime-owned;
written only by the worker, never by `atlas source sync`), via `storage.checkpoints`. A connector
opts in through two hooks: `next_checkpoint(content, prior)` (the cursor to persist after a
successful fetch) and `filter_entries(entries, checkpoint)` (skip already-seen items — an
optimisation; the parse-job dedupe stays the correctness gate). The worker injects the cursor into
`FetchContext.checkpoint` before fetch and persists `next_checkpoint` in the job's transaction.
- **NVD** windows server-side: `lastModStartDate` (from the cursor) + `lastModEndDate`
  (`FetchContext.now`); the **keyed tier** sends `credential_ref` → `NVD_API_KEY` as the `apiKey`
  header via `SafeFetcher.extra_headers` (the key is redacted in logs/columns).
- **SEC** records the newest `filingDate`/accession and filters strictly-older filings (keeping
  same-day so nothing unseen is missed).
- **RSS/FRED** keep the ETag/Last-Modified path (the hooks default to no-ops).

### 3.7 `healthcheck()` — feed source_health

Reports liveness / quality signals that the freshness job folds into
`source_health` (spec 2.12): `last_attempt_at`, `last_success_at`,
`last_item_published_at`, `freshness_lag`, `consecutive_failures`,
`success_rate_24h`, `parse_quality`, `duplicate_rate`, `item_count_recent`, and the
derived `status` (`ok|warn|stale|error|cooling_down`). The dedicated freshness job
(`job_type=freshness`) compares `freshness_lag` against the source's `freshness`
thresholds (§4.3) and is what surfaces a silently-stale feed in the daily digest.

---

## 4. Canonical source YAML

The git-tracked YAML under source control is the **source of truth**; it is mirrored
into the `source` table (plus `source.config` jsonb for fields that have no dedicated
column — see §4.6). This is the exact shape from the spec.

### 4.1 Full canonical example

```yaml
id: sec_edgar_companyfacts
name: SEC EDGAR Company Facts
category: finance_filings
access_method: api
base_url: https://data.sec.gov
enabled: true
policy:
  status: approved
  robots: respect
  allowed_uses: [internal_search, internal_summary, agent_retrieval]
rate_limit: { max_requests: 10, window_seconds: 1 }
freshness: { target: "24h", warn_after: "36h", error_after: "72h" }
retention: { raw: "2y", normalized: "permanent" }
credentials: { ref: null }       # a name resolved from env at runtime; NEVER a secret value
fetch:
  connector: atlascore.connectors.sec.companyfacts:SecCompanyFactsConnector
  schedule: "*/15 * * * *"
  timeout_seconds: 30
```

### 4.2 Field-by-field reference

| Field | Type | Required | Maps to | Notes |
|---|---|---|---|---|
| `id` | string (slug) | yes | logical key | Stable, lowercase, `provider_dataset` style. Used in `dedupe_key`s and YAML filename. |
| `name` | string | yes | `source.name` (**unique**) | Human-readable. Uniqueness enforced by `unique(name)`. |
| `category` | string | yes | `source.category` | E.g. `news_rss`, `macro`, `finance_filings`, `security_cve`, `code_releases`, `research`. Indexed `btree(category, enabled)`. |
| `access_method` | enum | yes | `source.access_method` | One of `api \| rss \| html \| browser`. `html`/`browser` are rare, approved exceptions; **no `html`/`browser` source ships in MVP** (§10). |
| `base_url` | string (https) | yes | `source.base_url` | Scheme must be `https` (or `http` only for local fixtures). |
| `enabled` | bool | yes | `source.enabled` | Scheduler only enqueues `enabled=true` **and** policy-allowed sources. Indexed with `category`. |
| `policy.status` | enum | yes | `source.policy_status` | `approved \| restricted \| pending \| unknown \| prohibited`. **Default `unknown`** if absent → disabled. |
| `policy.robots` | enum | no | `source.config.policy.robots` | `respect \| ignore_with_reason`. **Policy input / advisory** for API/RSS; a runtime robots gate is only added when an HTML/browser source is approved (§10). |
| `policy.allowed_uses` | string[] | yes (for approved) | `source.config.policy.allowed_uses` | E.g. `internal_search`, `internal_summary`, `agent_retrieval`. Round-trips into `source.config`. |
| `rate_limit.max_requests` | int | yes | `source.rate_limit` jsonb | Token-bucket size per window. |
| `rate_limit.window_seconds` | int | yes | `source.rate_limit` jsonb | Window length. SEC EDGAR = `{10, 1}` → 10 req/s. |
| `freshness.target` | duration | yes | `source.freshness` jsonb | Expected max age of newest item / max interval between successful fetches. |
| `freshness.warn_after` | duration | yes | `source.freshness` jsonb | `freshness_lag` past this → `source_health.status=warn`; appears in daily digest. |
| `freshness.error_after` | duration | yes | `source.freshness` jsonb | Past this → `status=stale/error`; fires a freshness alert. |
| `retention.raw` | duration \| `permanent` | yes | `source.retention` jsonb | Raw payload lifetime; typically `1y`–`2y` then prune. |
| `retention.normalized` | duration \| `permanent` | yes | `source.retention` jsonb | Canonical records lifetime; usually `permanent`. |
| `retention.audit` | duration | no | `source.retention` jsonb | Optional. Routine audit rotation hint; purge-class audit records are always retained (spec 2.11 / §10). |
| `credentials.ref` | string \| null | yes | `source.credential_ref` | An **env key name** resolved at runtime (e.g. `FRED_API_KEY`). **Never a secret value.** `null` for keyless sources. |
| `fetch.connector` | string | yes | `source.config.fetch.connector` | `module.path:ClassName`. The fetch connector. Distinct from `parser_ref` (a parser, not a fetcher). |
| `fetch.schedule` | cron string | yes | `source.config.fetch.schedule` | Per-source cron the scheduler reads to compute due sources. Must live in the table (§4.6). |
| `fetch.timeout_seconds` | int | yes | `source.config.fetch.timeout_seconds` | Per-request timeout passed to the httpx client. |

> The legacy scalar `retention_ref` string is **removed everywhere** (spec R6).
> Retention is always the structured `retention` jsonb above.

### 4.3 Retention & freshness duration grammar

Durations use a compact grammar (spec §4):

```text
duration := <N>d | <N>w | <N>m | <N>y | "permanent"
            d = days, w = weeks, m = months, y = years
            "permanent" is valid for retention only (not for freshness)
```

Examples: `36h`* `72h`* `7d`, `2w`, `6m`, `2y`, `permanent`.

> *Freshness thresholds in the canonical example use hour values (`24h`, `36h`,
> `72h`). The validator therefore also accepts `<N>h` (hours) for `freshness.*`
> fields; retention uses the `d/w/m/y/permanent` grammar above. Both are normalized
> to an interval at load time. `freshness_lag = now - coalesce(last_item_published_at,
> last_success_at)`.

### 4.4 Policy states & default-deny

| `policy.status` | Fetched? | Meaning |
|---|---|---|
| `approved` | **yes** | Allowed within the source's terms. |
| `restricted` | **yes, constrained** | Allowed only under encoded constraints (e.g. tighter rate, narrower endpoints). The connector must enforce them. |
| `pending` | no | Under review; disabled. |
| `unknown` | no | **Default.** Missing/unknown policy is disabled. |
| `prohibited` | no | Never fetched. |

Default-deny: a missing `policy` block, or any value other than `approved` /
`restricted`, means the source is **disabled** regardless of `enabled: true`. Only
local fixtures bypass this gate (offline unit/contract tests). The CLI enforces this
in `atlas source validate` and `atlas source test`; the scheduler enforces it before
enqueueing.

### 4.5 Credentials

- `credentials.ref` is an **env variable name**, resolved at runtime by
  `packages/security/secrets`. The YAML and the `source` table store the *name*,
  never the value (`source.credential_ref` is `text or null`, never a secret).
- Source YAML must contain **no secrets and no private endpoints**.
- Secrets come from `.env` (gitignored) → env vars, optionally from an
  age/SOPS-encrypted file whose decryption key lives outside the repo. The
  `EMBEDDING_API_KEY` / `LLM_API_KEY` and a source's `credentials.ref` key (e.g.
  `FRED_API_KEY`) are resolved the same way.

### 4.6 Source-table round-trip

The YAML must round-trip losslessly into the `source` table (spec 2.1 + correction
9). Fields with a dedicated column map directly; everything else lives in a
`source.config` jsonb column:

| YAML field | Storage |
|---|---|
| `name`, `category`, `access_method`, `base_url`, `enabled` | dedicated columns |
| `id` (slug) | `source.config.slug` — the table PK `id` is a generated **UUIDv7**; the YAML slug is the *logical* key (used for the filename + dedupe_keys), round-tripped in `config` ([ADR 0013](../decisions/0013-source-registry-on-disk-and-slug-round-trip.md)). Upserts are idempotent on the unique `name`. |
| `policy.status` | `source.policy_status` |
| `rate_limit`, `freshness`, `retention` | dedicated jsonb columns |
| `credentials.ref` | `source.credential_ref` |
| `policy.robots`, `policy.allowed_uses` | `source.config` jsonb |
| `fetch.connector`, `fetch.schedule`, `fetch.timeout_seconds` | `source.config` jsonb |

> `parser_ref` (spec 2.1) names the **parser**, not the fetch connector. The fetch
> connector and the per-source cron `schedule` come from `fetch.*` and live in
> `source.config`; the scheduler reads `source.config.fetch.schedule` to compute due
> sources. Without this, the scheduler (B1/B2/§5) would have nowhere to read the
> per-source schedule.

---

## 5. Concurrency, rate limiting & SSRF (connector-relevant subset)

> Full detail is in `docs/architecture/README.md` and `docs/security/README.md`;
> repeated here because connectors live inside these guarantees.

### 5.1 Single fetch worker (MVP)

- **Scheduler:** exactly one instance (single systemd unit / single compose service
  / pg advisory lock at startup). Two schedulers double-fire.
- **Fetch worker:** MVP runs **exactly one** fetch worker, so fetch concurrency per
  host = 1. Per-host **in-process token buckets** enforce `rate_limit`. Parse and
  enrich jobs may scale out (they do no source I/O).
- **Startup guard (spec correction 16):** the fetch role acquires a pg advisory lock
  (or asserts `FETCH_WORKERS=1`) at startup and **refuses to start a second fetch
  worker** until the documented scale-up path is enabled. This is what makes the SEC
  EDGAR "10 req/s across workers" guarantee true.
- **Scale-up (documented, not MVP):** to run >1 fetch worker, switch to a
  Postgres-backed per-host token bucket (`host_rate_state` table) or
  `pg_advisory_xact_lock(hashtext(host))` + decrement-on-claim. Until then, the
  guard blocks the second worker.

### 5.2 Backoff & circuit breaking

- Exponential backoff **with jitter** on transient errors (DNS/connect/read, 408,
  429, 5xx). Always respect `Retry-After`.
- Do **not** retry most 4xx (400/401/403/404) — fail fast, surface in the digest.
- After N consecutive failures a source is circuit-broken
  (`source_health.status=cooling_down`) and appears in the daily digest.
- Jobs exceeding `max_attempts` move to `crawl_job.status=dead` (DLQ), inspectable
  via the CLI.
- Claimed-but-stale jobs (`claimed_at` older than the visibility timeout) are
  reclaimed to `pending`.

### 5.3 Idempotency

- Deterministic `dedupe_key` per job; `unique(dedupe_key)` on `crawl_job`.
- Content-hash upserts; source-native unique keys (GUID / canonical URL / external
  id) so replays never duplicate.

### 5.4 SSRF / URL-safety (binding for the fetcher and any agent-supplied URL)

Every outbound request — connector fetches **and** any agent-supplied URL — passes
through the URL-safety guard in `packages/security/url_safety` (spec correction 6):

- **Scheme allowlist:** `http`/`https` only. All other schemes rejected.
- **Reject** loopback (`127.0.0.0/8`, `::1`), RFC1918 private ranges, CGNAT
  `100.64.0.0/10`, link-local `169.254.0.0/16` and `fe80::/10`, ULA `fc00::/7`,
  IPv4-mapped IPv6, and cloud-metadata endpoints `169.254.169.254` and
  `fd00:ec2::254`.
- **Resolve the hostname and validate ALL resolved IPs before connect, then pin to
  the validated IP** (anti-DNS-rebinding).
- **Re-validate every redirect hop** and **cap the redirect count**.

A URL that fails any check fails `validate()` and is not retried.

---

## 6. Connector SDK interface

`packages/connectors_sdk` defines the abstract base and result types. Connectors are
dependency-injected with their `httpx` client (already SSRF-guarded and rate-limited),
the clock, settings, and the source-registry row. They hold **no global state** and
make **no network calls in `__init__`**.

```python
# packages/connectors_sdk — illustrative interface (final signatures fixed in B1/B4)
from abc import ABC, abstractmethod
from collections.abc import Iterable

class Connector(ABC):
    """One subclass per source family. Pure stages; network only in fetch()."""

    def discover(self, ctx: "FetchContext") -> Iterable["JobSpec"]:
        """Optional. Enumerate fetchable units -> child fetch jobs. Default: none."""
        return ()

    @abstractmethod
    def fetch(self, ctx: "FetchContext") -> "FetchResult":
        """Network I/O via the injected, SSRF-guarded, rate-limited client.
        Send conditional headers from checkpoint. Return raw bytes + metadata,
        or a NotModified marker on 304."""

    @abstractmethod
    def validate(self, raw: "FetchResult") -> None:
        """Reject on bad status / MIME / size / SSRF re-check. Raise on failure."""

    @abstractmethod
    def parse(self, raw: "RawDocument") -> Iterable["ParsedItem"]:
        """Pure, deterministic, no network. Carries parser_version."""

    @abstractmethod
    def normalize(self, items: Iterable["ParsedItem"], ctx: "FetchContext") -> "NormalizeResult":
        """Map to canonical records with attribution, hashes, dedupe keys."""

    @abstractmethod
    def checkpoint(self, result: "NormalizeResult", ctx: "FetchContext") -> "Checkpoint":
        """Persist ETag/Last-Modified/cursor for incremental resume."""

    @abstractmethod
    def healthcheck(self, ctx: "FetchContext") -> "HealthSignals":
        """Emit signals folded into source_health by the freshness job."""
```

The SDK also ships a **contract-test harness** (§8) that runs a connector against
recorded fixtures and asserts lifecycle behavior without live network.

---

## 7. Per-connector notes

Each connector documents: access method, policy posture, the **provider's documented
limits** (respected verbatim), conditional-request support, canonical output table,
and quirks. None of these connectors touches social platforms, paywalls, or licensed
wires.

### 7.1 RSS / Atom — `packages/connectors/rss`

- **Access:** `rss`. The generic feed connector; most news/blog/vendor-advisory
  sources use it.
- **Policy:** per-source; many feeds are `approved` for internal search/summary.
  Robots is advisory policy input here (API/RSS path), not a runtime gate.
- **Limits:** no formal API rate limit; be polite — short windows
  (e.g. `{1, 1}`–`{1, 5}`) and conditional requests. Honor `Retry-After` if served.
- **Conditional requests:** **ETag** and **Last-Modified** are first-class — store
  them on `raw_document`, send `If-None-Match` / `If-Modified-Since`. A `304` is a
  cheap no-op.
- **Output:** `document` rows via the **article parser** (trafilatura + fallback).
  Dedupe on feed **GUID** and on `canonical_url` + parsed `content_hash`.
- **Quirks:** missing/duplicate GUIDs, partial-content feeds, relative URLs,
  timezone-naive dates (preserve source-local + normalize to UTC), feed-drift
  (schema changes mid-stream — see fixtures).

> **Implemented (B5).** The fetch worker enqueues one `parse` job per GUID keyed
> `parse:<slug>:<guid>:<raw_content_hash>` (content-aware, so a same-GUID in-place edit
> re-parses). The **network-free** parse worker (`worker/parse.py`) reads the immutable raw,
> extracts each entry's richest body (`FeedEntry.content_html`: `content:encoded` ↦ `summary`),
> runs `parsers/article.ArticleParser` (trafilatura + the nh3 `strip_html` fallback that does
> the work for short feed summaries), and upserts a `document` last-write-wins on `canonical_url`
> (R2; content-hash + GUID-aware dedupe for link-less items). `atlas parse <raw_id>` re-parses a
> whole raw.

### 7.2 FRED (Federal Reserve Economic Data) — `packages/connectors/fred`

- **Access:** `api` (JSON). Macro time-series.
- **Policy:** `approved` for internal use; requires a free API key.
- **Limits:** FRED requires an **API key**, passed as the `api_key` query param.
  FRED's documented ceiling is **120 requests per 60 seconds**; AtlasCore configures
  a conservative `rate_limit` (e.g. `{ max_requests: 100, window_seconds: 60 }`).
- **Credentials:** `credentials.ref: FRED_API_KEY` → resolved from env. **The
  `api_key` query param is a secret** — it must be stripped by `sanitize_for_storage`
  before any URL is written to `crawl_job.last_error`, `crawl_job.payload`, or
  `audit_log.detail` (§11 / spec correction 8).
- **Conditional requests:** use FRED's `realtime_start` / `realtime_end` and an
  observation-date cursor for incremental pulls; ETag where served.
- **Output:** `time_series_point` rows (`kind=observation`, scalar `value`) via the
  **macro parser**. Idempotent upsert on
  `UNIQUE NULLS NOT DISTINCT (provider, series_id, ts, vintage)`. Revisable series
  carry `vintage` (data as-of); the common case is `vintage=null`.
- **MVP role:** powers `get_macro_series` (B9) and the `macro_release` alert (B13).

### 7.3 SEC EDGAR — `packages/connectors/sec`

- **Access:** `api` (JSON + filing documents). Company Facts, submissions, and the
  daily filings index.
- **Policy:** `approved`; public US filings. No key required.
- **Limits — strict and mandatory:**
  - **Rate limit: 10 requests per second**, enforced host-scoped
    (`rate_limit: { max_requests: 10, window_seconds: 1 }`). The single-fetch-worker
    guard (§5) keeps this true.
  - **A descriptive `User-Agent` with contact info is required.** SEC may block
    requests without one. AtlasCore sends `DEFAULT_USER_AGENT` (transparent project
    UA + contact email). Never send a deceptive or rotating UA.
- **Conditional requests:** ETag / Last-Modified on the JSON endpoints; cursor over
  the daily index by date.
- **Output:** `document` rows via the **filing parser** (filing index → filing
  documents); Company Facts financial concepts may also land as
  `time_series_point` rows where they're genuine series. Dedupe on accession number
  (external id) + `content_hash`.
- **PDF/large filings:** parsed only via sandboxed Tika (non-root, CPU/mem/time
  limits) when filings are ingested as files.
- **MVP role:** B10 (basic filings searchable); powers the `sec_filing` alert (B13).

> **Implemented (B10) — `atlascore.connectors.sec.submissions.SecSubmissionsConnector`** ([ADR 0020](../decisions/0020-b10-sec-nvd-document-connectors-and-output-kind-dispatch.md)). `output_kind=document`. Fetches `/submissions/CIK##########.json` for `fetch.params.cik` (zero-padded); each recent filing → a searchable `document` via the shared B5 pipeline (accession = GUID, the primary-doc URL on `www.sec.gov` = canonical URL, `form — company` = title, a `"<desc> (form <F>) filed by <co> on <date>"` body). Sends the required contact `User-Agent` (`DEFAULT_USER_AGENT`) + 10 req/s; no key. **"Basic":** a per-filing metadata document, not full filing-text extraction (sandboxed Tika is a later follow-up). No conditional requests yet (idempotent re-fetch by GUID + content hash).

### 7.4 NVD (National Vulnerability Database) — `packages/connectors/nvd`

- **Access:** `api` (CVE 2.0 JSON API).
- **Policy:** `approved`; public CVE data.
- **Limits — public vs keyed:**
  - **Without an API key:** NVD allows roughly **5 requests per 30-second rolling
    window**. Configure conservatively (e.g. `{ max_requests: 5, window_seconds: 30 }`).
  - **With a free API key:** roughly **50 requests per 30-second window**.
  - NVD explicitly asks clients to **sleep between requests** and use the
    `lastModStartDate` / `lastModEndDate` incremental window rather than full pulls.
- **Credentials:** `credentials.ref: NVD_API_KEY` (or `null` for the keyless tier).
  The key is sent as the `apiKey` **header**, not a query param — still stripped from
  storage by `sanitize_for_storage` (§11).
- **Conditional requests:** drive incrementally with the `lastModStartDate` /
  `lastModEndDate` window cursor; respect `Retry-After` on 403/429.
- **Output:** `document` rows (CVE description + metadata as evidence) and
  `entity`/`mention` for affected products/vendors. Dedupe on CVE id + `content_hash`.
- **MVP role:** B10 (CVEs searchable); powers the `cve_watch` alert (B13, NVD +
  vendor RSS).

> **Implemented (B10) — `atlascore.connectors.nvd.cves.NvdCveConnector`** ([ADR 0020](../decisions/0020-b10-sec-nvd-document-connectors-and-output-kind-dispatch.md)). `output_kind=document`. Fetches the CVE 2.0 JSON API; each vulnerability → a searchable `document` (CVE id = GUID, the NVD detail URL = canonical URL, the **English** description = body). Optional `fetch.params.keyword` (`keywordSearch`) / `results_per_page`. **B10 runs the keyless tier** (`rate_limit: {5, 30}`); the keyed `apiKey`-**header** tier needs custom-header support in `SafeFetcher` and is a documented follow-up. No conditional/`lastMod` cursor yet (idempotent re-fetch by CVE id + content hash).

### 7.5 GitHub — `packages/connectors/github`

- **Access:** `api` (REST/GraphQL) for releases and security advisories of watched
  repos.
- **Policy:** `approved`; public repos / public advisories only. No login-walled or
  private content.
- **Limits — primary & secondary:**
  - **Primary rate limit:** unauthenticated ≈ **60 requests/hour**; authenticated
    with a token ≈ **5,000 requests/hour**. Configure per the chosen tier.
  - **Secondary (abuse) rate limits:** GitHub also enforces secondary limits on
    burst/concurrency; honor `Retry-After` and the `X-RateLimit-Remaining` /
    `X-RateLimit-Reset` headers, and back off when they near zero.
- **Credentials:** `credentials.ref: GITHUB_TOKEN` (a fine-grained read-only PAT) or
  `null` for the unauthenticated tier. Token sent as `Authorization` header — **never
  stored**; stripped by `sanitize_for_storage` (§11).
- **Conditional requests:** ETag is first-class on the GitHub API and a **304 does
  not count against the primary rate limit** — always send `If-None-Match`.
- **Output:** `document` rows (release notes / advisory text) and `entity`/`mention`
  for affected packages. Dedupe on release id / advisory GHSA id + `content_hash`.
- **Quirks:** GraphQL has its own point-budget cost model; the connector reads the
  cost headers and paces accordingly.

### 7.6 arXiv — `packages/connectors/arxiv`

- **Access:** `api` (the arXiv export Atom API), or its Atom feed per category.
- **Policy:** `approved`; open-access research metadata + abstracts.
- **Limits — be gentle:** arXiv asks clients to make **no more than one request
  every ~3 seconds** and to use a single connection; bulk harvesting should use OAI-PMH
  with `resumptionToken`. Configure `rate_limit: { max_requests: 1, window_seconds: 3 }`.
  Send the project `User-Agent`.
- **Credentials:** none (`credentials.ref: null`).
- **Conditional requests:** paginate via `start` / `max_results`; for OAI-PMH use the
  `resumptionToken` and `from` date cursor for incremental harvests.
- **Output:** `document` rows (title + abstract + authors as attribution; abstracts
  only — no full-text PDF harvesting in MVP) via the article parser. Dedupe on arXiv
  id (incl. version suffix) + `content_hash`.

> **Implemented (v1).** `connectors/arxiv/papers.py:ArxivConnector` **subclasses `RssConnector`** —
> the arXiv export API (`/api/query`) returns Atom, so feedparser parsing is reused verbatim and only
> the query-URL build differs (`fetch.params.search_query` + optional `max_results`/`start`/
> `sort_by`/`sort_order`). `sources/arxiv_cs_lg.yaml` (cs.LG recent papers). Each entry's
> `http://arxiv.org/abs/…` `<id>` is the GUID; `<summary>` (abstract) is the document body. Verified
> live: a `cat:cs.LG` fetch ingested 50 papers, searchable via `search_documents`.

> **Connector limits summary (configured `rate_limit`):**
>
> | Connector | Tier | Documented limit | `rate_limit` config | Key | Conditional |
> |---|---|---|---|---|---|
> | RSS | — | polite, no formal cap | `{1, 1}`–`{1, 5}` | none | ETag + Last-Modified |
> | FRED | keyed | 120 / 60s | `{100, 60}` | `FRED_API_KEY` (query) | realtime cursor |
> | ECB | no key | polite (keyless) | `{5, 1}` | none | idempotent upsert |
> | SEC EDGAR | public | **10 / 1s** + UA required | `{10, 1}` | none (UA required) | ETag, date cursor |
> | NVD | no key | ~5 / 30s | `{5, 30}` | optional `NVD_API_KEY` (header) | lastMod window |
> | NVD | keyed | ~50 / 30s | `{50, 30}` | `NVD_API_KEY` (header) | lastMod window |
> | GitHub | unauth | ~60 / hour | per tier | optional `GITHUB_TOKEN` (header) | ETag (304 free) |
> | GitHub | auth | ~5,000 / hour + secondary | per tier | `GITHUB_TOKEN` (header) | ETag (304 free) |
> | arXiv | public | ~1 / 3s | `{1, 3}` | none | start/resumptionToken |
>
> Provider numbers are the published guidance at 2026-06-03; AtlasCore always
> configures **at or below** the documented limit and respects `Retry-After`.

### 7.7 ECB (European Central Bank) — `packages/connectors/ecb` (v1)

- **Access:** `api` (CSV via the ECB Data Portal). Euro-area macro time-series.
- **Policy:** `approved`; free, official, **keyless** public data.
- **Limits:** no published hard cap; be polite (`{5, 1}`) and honor `Retry-After`.
- **Credentials:** none — `credentials.ref: null`; nothing secret ever rides in the URL.
- **Addressing:** `fetch.params.flow_ref` (e.g. `EXR`) + `series_key` (e.g. `D.USD.EUR.SP00.A`)
  build `/service/data/{flow}/{key}?format=csvdata`; `params.series_id` is the stable id stored on
  each point.
- **Output:** `time_series_point` rows (`kind=observation`) via the macro path — the same idempotent
  upsert as FRED. **The unit comes from the CSV `UNIT` column** (no operator label needed).
  TIME_PERIOD parsing covers daily/monthly/quarterly/annual; a missing `OBS_VALUE` → `null` (never
  zero-filled, §15).
- **Role:** powers `get_macro_series` + the `macro_release` alert, same as FRED.

> **Implemented (v1).** `connectors/ecb/series.py:EcbConnector` (`output_kind=timeseries`);
> `sources/ecb_eur_usd.yaml` (EUR/USD daily reference rate). Verified live: a full-history fetch
> upserts ~7k points with `provider=ecb`, `unit=USD`.

---

## 8. Fixtures policy & parser golden tests

No live network in unit/contract tests (spec §19). Every connector ships a fixed set
of recorded fixtures, and every source has at least one parser golden-output test.

### 8.1 Required fixtures per connector (six classes)

Stored under `tests/fixtures/<connector>/`:

| Fixture | Asserts |
|---|---|
| **success** | A normal payload parses & normalizes to the expected canonical rows. |
| **empty** | An empty-but-valid response (no items / `304`) produces no new rows and no error. |
| **malformed** | Broken/invalid payload (bad XML/JSON, truncated) fails `parse()` cleanly — no partial write, job retries then `dead`, no crash. |
| **rate-limit** | A `429`/`403` with `Retry-After` triggers backoff and respects the delay; no retry storm. |
| **drift** | Source schema change (renamed field, new wrapper, reordered keys) is detected and either tolerated or fails loudly — never silently mis-parses. |
| **deleted / unavailable** | A `404`/`410` or removed item is handled gracefully (no duplicate, no crash); existing data is not deleted as a side effect. |

HTTP interactions are recorded with `respx` / `vcrpy`. Fixtures **never** contain
secrets, private URLs, or personal data (sanitize before committing).

### 8.2 Parser golden-output test (one per source)

For each source/parser, a **golden test** pins the canonical output: feed the
recorded raw payload through the pinned `parser_version` and assert the normalized
records byte-for-byte (modulo non-deterministic fields, which are injected — clock,
ids). A golden test:

- Lives under `tests/unit/parsers/<source>/` with its golden file alongside.
- Is regenerated only by an explicit, reviewed action when the parser intentionally
  changes (bump `parser_version`).
- Is the regression net required by the spec: any bug fix adds a regression test.

### 8.3 Contract tests

The SDK harness (§6) runs each connector's full lifecycle against its fixtures and
asserts: policy gate refuses non-approved sources, conditional headers are sent,
SSRF guard is invoked, dedupe keys are deterministic, and the run is idempotent
(re-run inserts zero new rows).

---

## 9. SSRF guard reference (binding)

Restated here because it gates every connector fetch and any agent-supplied URL
(spec correction 6). The guard in `packages/security/url_safety`:

1. **Scheme allowlist:** `http`/`https` only.
2. **Block** loopback, RFC1918, CGNAT `100.64.0.0/10`, link-local
   `169.254.0.0/16` + `fe80::/10`, ULA `fc00::/7`, IPv4-mapped IPv6, and metadata
   endpoints `169.254.169.254` / `fd00:ec2::254`.
3. **Resolve + validate every resolved IP before connect; pin to the validated IP**
   (anti-rebinding).
4. **Re-validate every redirect hop; cap redirect count.**
5. Applies to **the fetcher and any agent-supplied URL** (the latter also requires
   passing source policy before fetch).

A security regression suite (B15) covers SSRF, the sanitizer, secrets-in-logs, and
prompt-injection fixtures.

---

## 10. Compliance posture for connectors

- **Internal-only:** ingested data is never redistributed; always keep attribution
  (source, URL, author, timestamp).
- **No social ingestion, no paywalls, no licensed wires, no broker APIs** in this
  build. If a social signal is ever wanted, use an aggregator vendor so ToS sits
  with them.
- **Robots:** a **policy input** consulted by the human when setting `policy.status`
  (and at runtime only for the rare permitted HTML/browser source). It is **not** a
  general runtime gate for API/RSS sources. **No HTML/browser source ships in MVP**
  (B1–B15), so **no runtime robots gate is built in the MVP**; `policy.robots` stays
  advisory until an HTML source is approved — at which point a runtime robots check
  is added inside that connector's block.
- **PII (honest framing):** article/CVE/filing full text inherently contains personal
  data (names, quotes). AtlasCore does **not** claim to strip all PII. The posture is:
  (a) GDPR personal/household exemption while purely personal and unpublished;
  (b) data minimization where feasible — no social, no profiling, drop obviously
  unneeded personal fields at parse; (c) a working, audited erasure path
  (`atlas purge`, incl. raw + full cascade); (d) the exemption lapses if results are
  published — revisit then.
- **Cloud backends & PII egress (spec correction 5):** when `EMBEDDING_BACKEND` or
  `LLM_BACKEND` is a cloud provider, document text — which contains PII — egresses to
  a third-party (likely third-country) processor. For PII-bearing documents prefer
  **local** backends; if cloud is used, configure no-training / zero-retention / DPA
  settings, and do **not** describe cloud LLM/embedding use as consistent with a pure
  household exemption. This is documented further in `docs/compliance/README.md`.
- **FOSS boundary:** publish connector code and schemas, never harvested data; no
  hardcoded credentials or licensed feeds in the repo.

---

## 11. Secrets never reach storage

Connectors deal with credential-bearing URLs and headers (FRED `?api_key=`, NVD
`apiKey` header, GitHub `Authorization`). Before persisting `crawl_job.last_error`,
`crawl_job.payload`, or `audit_log.detail`, the connector/worker passes the value
through the single **`sanitize_for_storage`** helper in `packages/security/secrets`
(spec correction 8):

- Strip URL **query** and **userinfo**, and `Authorization` / `Cookie` headers.
- Redact known-secret patterns (e.g. `api_key=...`, `apiKey: ...`, bearer tokens).
- **Forbid** storing resolved credential values or credential-bearing URLs in any
  jsonb/text column. **Backups inherit this** — a `pg_dump` must never contain a key,
  because the repo-only `gitleaks`/`detect-secrets` scan never sees Postgres content.
- The B15 secrets-in-logs regression test covers `last_error` and `audit_log.detail`.

---

## 12. Add-a-source workflow

The end-to-end procedure (spec §21 "Add a source", reconciled to this doc):

1. **Decide policy.** Determine the source's legal/ToS posture. Set
   `policy.status: pending` unless it is genuinely `approved`. Record
   `allowed_uses`, `robots` posture, attribution, retention, PII risk, and the
   documented rate limit. Default-deny if unsure.
2. **Write the YAML.** Create `<id>.yaml` under the registry directory using the
   canonical shape (§4). `credentials.ref` names an env key or is `null` — never a
   secret. No private endpoints.
3. **Validate.** Run `atlas source validate path/to/<id>.yaml` — checks the schema,
   duration grammar, policy enum, and round-trip to the `source` table shape. It
   **refuses** to enable `unknown`/`pending`/`prohibited`.
4. **Implement the connector** via the SDK lifecycle (§3): `fetch` (conditional
   headers, SSRF-guarded client, rate limit), `validate`, `parse` (pinned
   `parser_version`), `normalize` (attribution, hashes, dedupe keys, one
   transaction), `checkpoint`, `healthcheck`. Reuse an existing connector
   (`rss`/`api`) where possible.
5. **Add fixtures + golden test.** All six fixture classes (§8.1) and one parser
   golden-output test (§8.2). No secrets/PII in fixtures.
6. **Add a health metric + short doc.** Ensure `healthcheck()` feeds `source_health`;
   add a per-source note in this file's §7 if the source is materially new.
7. **Dry-run.** `atlas source test <source_id>` exercises the connector against
   fixtures/mocked HTTP (no live network by default; live tests are opt-in and
   rate-limited).
8. **Enable.** Set `policy.status: approved` (or `restricted` with encoded
   constraints) and `enabled: true`. Only then does the scheduler enqueue it, reading
   `source.config.fetch.schedule`.
9. **Record the change.** Update `docs/STATUS.md` (claim/close), add a
   `CHANGELOG.md` `[Unreleased]` entry, and write an ADR only if the source is
   architecture-changing (new service/store).

Relevant CLI commands:

```bash
atlas source validate path/to/source.yaml   # schema + policy + grammar checks
atlas source test <source_id>                # run connector against fixtures/mocks
atlas crawl once <source_id>                 # one manual crawl (control plane)
atlas parse <raw_id>                         # re-parse an immutable raw doc
atlas enrich <doc_id>                        # run enrichment for a document
```

---

## 13. Cross-references

- `docs/architecture/README.md` — scheduler/worker/queue, single-instance & single
  fetch-worker invariants, the canonical flow.
- `docs/schemas/README.md` — the 12 canonical tables, including `source`,
  `crawl_job`, `raw_document`, `document`, `time_series_point`, `source_health`.
- `docs/security/README.md` — SSRF guard, HTML sanitizer, secrets handling,
  `sanitize_for_storage`.
- `docs/compliance/README.md` — internal-only posture, honest PII framing, cloud-PII
  egress, retention classes, `atlas purge`.
- `docs/mcp/README.md` — the 7 read tools that consume connector output.
- `docs/runbooks/README.md` — backups/restore, purge, backfill/reindex, scale-up of
  the fetch worker.
- `CLAUDE.md` — the agent operating contract and session rituals.
