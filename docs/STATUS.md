# AtlasCore — STATUS (living board)

> **This is the shared coordination board for every session (human or AI) and every worker.**
> Read [`../CLAUDE.md`](../CLAUDE.md) and this file before starting. Update it at session close.
> Last updated: **2026-06-05**.

---

## Current phase

**Post-MVP cycle B16–B22 — DONE (2026-06-05). MVP (B1–B15) complete. Scope frozen (CLAUDE.md §5); the next step is operational — deploy + a month-long unattended soak.**

The post-MVP upgrade cycle **B16–B22** added depth on existing components (not new sources/services, so
the §5 freeze is respected): audit_log rotation + crawl_job prune (B16), `time_series_point` yearly
partitions (B17), alert watchlist gating + lifecycle CLI (B18), the single-instance scheduler +
continuous worker loop (B19), connector depth — custom headers + NVD keyed tier + incremental checkpoint
(B20), real embedding/LLM backends + a hard spend cap + spaCy NER + `atlas reindex-embeddings` (B21),
and SEC full-filing extraction via sandboxed Tika (B22). Migrations 0010/0011/0012 applied + drift-free;
the deterministic offline backends remain the CI default. Full detail in the **Done — Post-MVP upgrade
cycle (B16–B22)** section below.

**2026-06-05 — full end-to-end run + two delivery/migration bug fixes.** A live run (seed → live Fed RSS
crawl → parse → enrich → search → 7 MCP tools → alerts → health/digest → backup/restore → purge), plus a
three-agent adversarial review, surfaced and fixed two real bugs — alert/digest delivery on a
blank/whitespace channel config, and offline migration SQL (`alembic upgrade head --sql`) crashing on
migration 0010 (both in Known issues, each with a regression). Full suite **397 passed, 1 skipped**;
ruff/mypy clean.

**MVP (B1–B15) — the foundation, completed 2026-06-05.** B15 adds the **security
regression gate** (`make security` → `pytest -m security`): a consolidated
`tests/unit/test_security_regressions.py` (SSRF incl. DNS-rebinding + metadata + scheme, HTML
active-content stripping, secrets-never-in-DB-columns over the `crawl_job`/`audit_log` shapes,
prompt-injection-as-evidence, and the read-only 7-tool MCP surface), with the existing
SSRF/sanitizer/`sanitize_for_storage` suites also tagged `security` and the B14 purge
multi-doc-summary regression — **the full `security` suite green**. The **end-to-end smoke/demo**
(`make smoke` → `pytest -m smoke`, `tests/integration/test_smoke.py`) runs the MVP done-when in one
unattended path with **no live network** (respx feed): register an approved source → fetch (raw
captured) → parse → enrich → search → cited MCP summary → alert → MCP `get_alerts` → backup → audited
purge. The README is rewritten to the as-built state ([ADR 0025](decisions/0025-b15-hardening-and-mvp-completion.md)).
**Done-when met:** source → ingest → search → MCP summary → alert runs unattended with backups, and
the security regressions are green — verified live.

**B15 polish (2026-06-05):** completed the **full 7-tool MCP surface** by shipping the last tool,
**`get_raw_document`** (`storage.raw_documents` + the MCP tool): sanitized text by default, verbatim
raw HTML only behind the gated `include_raw_html=true` flag (wrapped with an untrusted-evidence
notice + a distinct `mcp_raw_html_returned` audit action), byte-capped by `max_bytes`
([ADR 0026](decisions/0026-b15-polish-get-raw-document.md)). This closes the only MVP spec gap
(CLAUDE.md §13 mandates all 7 tools). **306 tests pass**; ruff/mypy clean; drift-check empty on live
PG16; no schema change.

**All of B16–B22 has since shipped** (see the cycle summary above and the dedicated section below), so
the items once "deferred past MVP" — the timed scheduler/worker loop, `audit_log` rotation, real
embedding/LLM backends, SEC full-filing (Tika), the NVD keyed tier, the alert watchlist + lifecycle CLI,
and `time_series_point` yearly partitions — are now all DONE. Per CLAUDE.md §5, scope is frozen until the
current set runs unattended for a month; the next step is operational, not another feature block.

---

### Prior phases

**Block B14 — DONE (2026-06-05).**

**Backups restore, and the audited erasure path works.** `atlas purge <doc_id|entity_id> --yes`
(`atlascore.storage.purge`) runs the full R3 cascade in one transaction: `embedding` → `mention` →
`summary` (`document_id` **OR** `member_doc_ids[]` — the **multi-doc-summary** case, the load-bearing
regression) → `document` (drops it from FTS) → `raw_document` (file + row, **only when no other
document references the shared raw** — one feed fetch backs N documents, RESTRICT forbids otherwise)
→ alert `evidence_doc_ids` reconciliation (an alert losing all evidence ⇒ `status='purged'`) → a
**retained** `audit_log` entry (`retention_class='purge'`, content hashes + counts only, never
personal data). An entity purge deletes the entity + its mentions and reconciles
`document.entity_ids[]`; documents survive. Purge is idempotent. `RawStore.delete` is the single
blob deleter. `atlas backup` writes a local `pg_dump` + raw-store copy (password via `PGPASSWORD`,
never argv); `infra/backup.sh` keeps offsite sync + retention; `infra/restore_drill.sh` restores a
dump into a throwaway scratch DB and verifies it
([ADR 0024](decisions/0024-b14-purge-cascade-backups-and-restore-drill.md)). **Done-when met:**
restore verified (live drill: 14 tables + rows restored into a scratch DB) and purge works + is
audited (verified live incl. the multi-doc-summary regression, shared-raw guard, alert reconciliation,
idempotency). **289 tests pass**; ruff/mypy clean; no schema change (alert was the last table, B13).

**Block B13 — DONE (2026-06-04).**

**Rule-based alerts fire with dedupe + evidence and are queryable via MCP.** Migration `0009` adds
the `alert` table (a **normal** `op.create_table` table — autogenerate/drift-checked, unlike the
raw-SQL enrichment/summary tables): `dedupe_key` (unique), `evidence_doc_ids text[]`, `detail` jsonb
(sanitized before write), `btree(status, severity, created_at)`. Four rules (`atlascore.alerts.rules`)
are evaluated idempotently (a deterministic `dedupe_key` ⇒ re-evaluation never double-fires):
`sec_filing` + `cve_watch` scan `document`s by source category and cite the document in
`evidence_doc_ids`; `macro_release` fires on the latest FRED observation per series; `freshness`
fires on stale/error `source_health` (closing the B8/§17 seam). One configured **delivery channel**
(`ALERT_WEBHOOK_URL`/`NTFY_TOPIC`/SMTP, else stdout/log) sends alerts **and now backs `atlas digest`**
(closing B8's stdout-only seam). `atlas alerts run` evaluates + delivers (control-plane,
MCP-independent); the read-only **`get_alerts`** MCP tool filters by type/status/`min_severity`/`since`,
caps at 100, resolves evidence→provenance, and is audited
([ADR 0023](decisions/0023-b13-rule-based-alerts-delivery-and-get-alerts.md)). **Done-when met:**
each alert fires with dedupe + `evidence_doc_ids` and is queryable via MCP — verified live (4 rules
fire, idempotent re-run creates 0, LogChannel delivery, `get_alerts` + min_severity/type filters).
**281 tests pass**; ruff/mypy clean; migration `0009` applies + drift-check empty on live PG16.

**Block B12 — DONE (2026-06-04).**

**Cached, cited document summaries are queryable via MCP.** Migration `0008` adds the `summary`
table (R4 content-hash cache keyed `unique(source_hash, model_name, model_version, prompt_version)`
over the **ordered** member `content_hash`es; `scope`, `member_doc_ids[]`, `citations`, with
`GIN(member_doc_ids)` for the R3 purge cascade) — raw SQL, excluded from the drift-check by owning
table. The summary pipeline (`atlascore.enrichment.summaries`) does **dedupe-before-summarize →
content-hash cache → compute-on-miss**: an identical `(content, model, prompt)` tuple is **never
re-summarized**, and a re-parsed member busts the cache. MVP's `local` backend is the deterministic,
dependency-free **`ExtractiveSummarizer`** (cited lead-sentence extraction, offline/CI); hosted LLMs
(`LLM_BACKEND=api`) are pluggable behind the `api` extra; `none` ⇒ cache-only. The
**`get_document_summary`** MCP tool (1–12 ordered docs, `allow_compute`) returns a cited summary
(`cache=hit|miss`) with member-source provenance, or `summary_text=null` + a reason when nothing is
cached and no backend is configured (never a hallucination); it computes-on-miss only when a backend
is set and **writes a `summary` row** then (a documented exception to the audit-only posture, gated
by `LLM_BACKEND`/`allow_compute`)
([ADR 0022](decisions/0022-b12-summaries-pipeline-and-content-hash-cache.md)). **Done-when met:** a
cached, cited summary via MCP, no re-summarization of an identical tuple — verified live (compute →
cache hit, single/multi scope, content-change bust, cache-only null+reason). **271 tests pass**;
ruff/mypy clean; migration `0008` applies + drift-check empty on live PG16.

**Block B11 — DONE (2026-06-04).**

**Documents are now enriched and the entity surface is queryable.** Migration `0007` adds the three
enrichment tables — `entity` (trigram `gin (canonical_name gin_trgm_ops)` + array GINs +
`merged_into`), `mention` (unique `(entity, document, ner-model+version)`), and `embedding`
(`pgvector vector(384)` + HNSW) — all via raw SQL like `0006`, excluded from the drift-check **by
owning table**. The enrichment pipeline (`atlascore.enrichment`) runs **language → NER → topics →
embeddings** in one transaction, writing `document.language`/`topics`/`entity_ids` and creating
entity/mention/embedding rows. MVP backends are **deterministic and dependency-free** (rule-based
NER + hashing embeddings) so it runs offline/in CI; spaCy/sentence-transformers/cloud are pluggable
behind the existing extras with no schema change. The parse worker enqueues one **idempotent**
`enrich` job per changed document (so fetch→parse→enrich runs unattended); `atlas enrich <doc_id>`
runs it directly. The **`get_entity_mentions`** MCP tool resolves by id → identifier → fuzzy name
(trigram + aliases, `merged_into`-aware), capped + audited, with sanitized snippets; the deferred
B7 `search_documents` `topics`/`entity_ids` filters are now wired
([ADR 0021](decisions/0021-b11-enrichment-entity-mention-embedding.md)). **Done-when met:** entity
mentions are queryable via `get_entity_mentions` — verified live (enrich → resolve by
id/name/ticker → mentions with provenance, idempotent re-embed skip, full queue path + CLI).
**260 tests pass**; ruff/mypy clean; migration `0007` applies + drift-check empty on live PG16.

**Block B10 — DONE (2026-06-04).**

**SEC EDGAR filings + NVD CVEs are searchable documents.** Both are `access_method=api` sources
that produce **documents** (not time-series), so B10 adds a **`Connector.output_kind`**
discriminator (`document` | `timeseries`): the single fetch worker (`run_one_fetch`) now dispatches
by what the connector *produces* — `timeseries` → `worker.macro` (FRED), else → the **B5 document
pipeline**. `run_fetch_job`'s `FetchContext` carries connector `params` + the resolved `credential`.
`SecSubmissionsConnector` fetches `/submissions/CIK##########.json` (CIK from `params.cik`, sends
the required contact UA + 10 req/s, no key); `NvdCveConnector` fetches the CVE 2.0 JSON API (keyless
~5 req/30 s). Both implement `parse_entries` → `FeedEntry` → the shared `ArticleParser` → searchable
`document` (no new parser, **no schema change**)
([ADR 0020](decisions/0020-b10-sec-nvd-document-connectors-and-output-kind-dispatch.md)).
**Done-when met:** filings + CVEs searchable via `search_documents` — verified live (full SEC + NVD
fetch → parse → search finds the 10-K filing, the buffer-overflow CVE, and the SQL-injection CVE).
**240 tests pass**; ruff/mypy clean; no migration; no new `detect-secrets` findings.

**Block B9 — DONE (2026-06-04).** The **macro time-series** path. Migration `0006` creates
`time_series_point` — the **first partitioned table** (composite PK `(id, ts)`, `UNIQUE NULLS NOT
DISTINCT`, DEFAULT partition; raw SQL; drift-check excluded by name prefix). The **FRED connector**
fetches observations → `Decimal` (`"."` → null); the connector ABC supports both families
(`parse_entries`/`parse_observations`); `FetchContext` carries `params` + `credential`. The fetch
worker dispatches RSS → documents, FRED → `worker.macro` (idempotent `upsert_observations`).
**`resolve_credential`** + sanitized `?api_key=…`; the **`get_macro_series`** MCP tool (value as
string, missing→null, capped, audited)
([ADR 0019](decisions/0019-b9-time-series-fred-connector-and-macro-series.md)).

**Block B8 — DONE (2026-06-04).** The **freshness/health subsystem**. Migration `0005` creates
`source_health` (current snapshot, PK `source_id`, `interval` lag, CHECK status, `btree(status)`).
`worker/freshness.refresh_source_health` computes + upserts each source's health; the pure
unit-tested `classify_status` orders `cooling_down` > `stale` > `warn` > `error` > `ok`.
`atlas health` / `atlas digest` (control-plane) recompute then read source_health + DLQ depth +
fetch-worker liveness (digest delivers to stdout/log — B13 wires the channel); `get_source_health`
is a read-only, **secret-free**, audited, capped mirror
([ADR 0018](decisions/0018-b8-source-health-freshness-and-digest.md)).

**Block B7 — DONE (2026-06-04).** Read-only **MCP server** (FastMCP, stdio) + the **`audit_log`**
table (migration `0004`; append-only, no FKs, sanitized `detail`). `search_documents` reuses B6's
query path (sanitized snippet + provenance, never raw HTML, `limit ≤ 50`, audited per call);
`atlas mcp dev` serves it ([ADR 0017](decisions/0017-b7-mcp-server-search-documents-and-audit-log.md)).

**Block B6 — DONE (2026-06-04).** Postgres FTS **query path** + `atlas search` CLI
(`storage.search.search_documents`: matches `ix_document_fts` exactly, `ts_rank`, `ts_headline`
`«…»` snippet + provenance, source/`--since`/`--until` filters, `MAX_LIMIT=100`; static
parameterized SQL; `EXPLAIN`-seqscan-off index-usage regression —
[ADR 0016](decisions/0016-b6-fts-query-path-and-search-contract.md)).

**Block B5 — DONE (2026-06-04).**

The article parser + canonical `document` + parse worker are in place. Migration `0003` creates the
`document` table (docs/schemas §2.4) with its partial-unique `canonical_url`, FTS `tsvector` GIN, and
array GINs created via raw SQL + excluded from the autogenerate drift-check (`_include_object` in
`migrations/env.py`). `parsers/article.ArticleParser` (trafilatura + sanitizer fallback; pinned
`article@1.0.0`) and the **nh3-based** `security/sanitization.strip_html` produce sanitized
`body_text`/title. The **network-free** parse worker (`run_parse_job`/`run_one_parse`/
`reparse_raw_document`, `atlas parse <raw_id>`) consumes B4's per-GUID parse jobs and upserts
documents last-write-wins (R2), URL + content-hash dedupe. **Done-when met:** documents created;
parser golden fixture in CI; full B4→B5 chain (fetch → raw → parse → document → FTS → idempotent
re-parse) verified on Postgres 16. **194 tests pass** (unit + contract + integration on a live DB);
ruff/mypy clean; migration applies + drift-check empty on live PG16.

A **multi-agent adversarial review** of B5 + the deferred B4 SSRF guard ran and confirmed 6 defects
(refuting 1 non-finding); **all 6 are fixed** with regressions — see CHANGELOG "Fixed / Security — B5":
the hand-rolled HTML sanitizer (active-content leak on adversarial markup → replaced with **nh3**),
a fetcher decompression-bomb gap (→ **streamed incremental size cap**), a same-GUID-edit stranded
parse (→ content-aware `dedupe_key`), a null-URL dedupe collapse (→ GUID-folded fingerprint), and an
unsanitized feed title. **B1–B4** are also DONE.

The repo is pushed to `github.com/RNT56/atlascore` (**private**) with **CI fully green (8/8 jobs)**.
The first-ever CI run surfaced two latent B1 workflow-config bugs, both fixed: a `runner.temp`
reference in a job-level `env:` that blocked the whole workflow from compiling, and `pip-audit --strict`
choking on the unpublishable local `atlascore` package.

The MVP is a single ordered backlog of 15 blocks (**B1–B15**). See
[`PROJECT-PLAN.md`](PROJECT-PLAN.md) §MVP for the authoritative list. Discipline: freeze scope
until the current set runs unattended for a month before adding sources or services.

---

## Done (2026-06-03)

- Planning consolidated into [`PROJECT-PLAN.md`](PROJECT-PLAN.md) (legacy `AtlasWire-Personal-Plan.md`
  renamed; conflicting build lists replaced by a pointer to the single B1–B15 backlog;
  `AtlasWire`/`atlaswire` renamed to `AtlasCore`/`atlascore` except one documented "Formerly AtlasWire" note).
- [`../CLAUDE.md`](../CLAUDE.md) agent operating contract written and reconciled to the canonical spec.
- Full `docs/` scaffold created: `STATUS.md`, and deep-dive `README.md` files under
  `architecture/`, `connectors/`, `schemas/`, `security/`, `compliance/`, `mcp/`, `runbooks/`.
- ADRs 0001–0011 plus `decisions/0000-template.md` and `decisions/README.md` index
  (dated 2026-06-03, status Accepted).
- CI (`.github/workflows/ci.yml`), `dependabot.yml`, `.pre-commit-config.yaml`,
  `pyproject.toml` (uv; `atlas` console script), `Makefile`, and root project files.
- Canonical repository tree created as folders (with `.gitkeep`).
- [`../README.md`](../README.md) and [`../CHANGELOG.md`](../CHANGELOG.md) seeded.
- **B1 — repo skeleton + packaging + Alembic + `source` schema (2026-06-03).**
  - Repository git-initialized; scaffold committed; `uv` toolchain on Python 3.12 with a committed
    `uv.lock`.
  - **Packaging finalized → ADR [0012](decisions/0012-packaging-layout-src-atlascore.md):** single
    `src/atlascore` import root (on-disk path == import path; the `packages`/`apps` split is kept as
    subpackage names). `atlas = atlascore.cli.main:app`. CLAUDE.md §7 + pyproject + CI/pre-commit updated.
  - `src/atlascore/core` (settings/ids/clock/logging/errors), `schemas` (`SourceRecord` + enums +
    default-deny), `storage.postgres` (`Base` + `Source` ORM), and the minimal `atlas` CLI.
  - Alembic harness + migration `0001` (extensions then `source`); URL resolved from env, no secrets.
  - 26 unit + 4 integration tests; audited `.secrets.baseline`; ruff/mypy/pytest all green.
  - Adversarial multi-agent review run; confirmed findings fixed (CI integration-job exit-5 redness;
    detect-secrets CI flag bug; constraint-naming locked by a unit test; doc/nit clarifications).
- **B2 — source registry + default-deny + canonical YAML + `atlas source` CLI (2026-06-03).**
  - `atlascore.core.durations` (retention/freshness grammar) + `SourceYAML` model (nested
    policy/credentials/fetch) with validators (durations, connector ref, 5-field cron, base_url
    scheme, slug) and `to_record()` (R9 round-trip; YAML slug → `config.slug`, [ADR 0013](decisions/0013-source-registry-on-disk-and-slug-round-trip.md)).
  - **Default-deny enforced**: `allowed_uses` required for approved/restricted; refuses
    `enabled: true` on a non-fetchable policy; `is_fetchable = enabled AND policy.is_fetchable`.
  - `atlascore.storage.sources` (load/discover/upsert [slug-keyed, ADR 0013]/get-by-slug/list) + the
    `atlas source validate` (pure, no DB; CI `source-yaml` target) / `sync` / `test` / `list` CLI.
  - The `sources/` registry with an approved public-domain RSS source (`fed_press_releases`),
    `sources/README.md`, and `tests/fixtures/sources/` (approved + refusal + malformed cases).
  - 40 new tests (durations, source YAML round-trip + default-deny, validate CLI, sync/get/list
    integration); ruff/mypy/pytest green; done-when demonstrated against a real Postgres 16.
  - CLAUDE.md §7 (`sources/`) + §9 (`source sync`/`list`) and docs/connectors §4.6 updated.
- **B3 — raw store + `crawl_job` queue + `sanitize_for_storage` (2026-06-03).**
  - Migration `0002`: `crawl_job` (status machine + DLQ, unique `dedupe_key`, sanitized
    `last_error`/`payload`) and `raw_document` (content-addressed, unique `content_hash`, FK RESTRICT).
  - `atlascore.storage.rawstore.RawStore`: immutable, gzipped, content-addressed (`<algo>/<shard>/
    <rest>.gz`), atomic write + idempotent dedupe. `core.hashing.content_hash`.
  - `atlascore.storage.queue`: `enqueue` (idempotent on `dedupe_key`), `claim_next`
    (`FOR UPDATE SKIP LOCKED`), `complete`, `fail` (retry→`pending` with exponential backoff, then
    →`dead`), `reclaim_stale`, `dead_jobs`. Injected `now` for deterministic retry timing.
  - `atlascore.security.secrets.sanitize_for_storage`: strips URL query/userinfo, redacts
    auth/cookie headers + bearer tokens + secret-keyed values (recursively). Routed in `enqueue`/`fail`.
  - `atlas crawl enqueue` (default-deny enforced) / `dlq`; shared `cli/_db.py` session helper.
  - 24 new tests (hashing, sanitize attack-cases, raw store, backoff; queue state machine + SKIP
    LOCKED + reclaim + raw_document dedupe integration). Migration drift-free + reversible; gates green.
- **B4 — RSS connector + SSRF-guarded fetcher + worker (2026-06-03).**
  - `atlascore.security.url_safety`: the SSRF guard (scheme allowlist; reject
    loopback/RFC1918/CGNAT/link-local/ULA/IPv4-mapped/metadata via `not is_global` + explicit checks;
    resolve + validate ALL IPs; per-redirect re-validation). Connection-pinning deferred —
    [ADR 0014](decisions/0014-ssrf-validate-all-ips-pin-deferred.md).
  - `connectors_sdk` (`Connector` ABC, `FetchContext`, `HostRateLimiter`, `SafeFetcher`): the
    SSRF-guarded `httpx` fetcher — conditional requests (ETag/Last-Modified), manual redirect handling
    with a cap, MIME allowlist + **streamed incremental size cap** (B5 review hardening).
  - `atlascore.connectors.rss.RssConnector` (feedparser; GUID extraction; fail-loud on non-feed).
  - `atlascore.worker` (`run_fetch_job`/`run_one_fetch`): fetch → store raw before parse → enqueue one
    `parse` job per GUID (idempotent dedupe) → checkpoint ETag/Last-Modified; claim/process in separate
    txns; single-fetch-worker advisory-lock guard (R16). `atlas crawl once`.
  - 17 new tests (SSRF, rate limiter, RSS parse, fetcher contract via respx, worker integration) +
    a 27-case SSRF audit. Verified LIVE against the Federal Reserve feed (fetch + 304 conditional + dedup).
- **B5 — article parser + canonical `document` + parse worker (2026-06-04).**
  - Migration `0003`: the `document` table (docs/schemas §2.4); partial-unique `canonical_url`, FTS
    `tsvector` GIN, and array GINs via raw SQL, excluded from the drift-check by `_include_object`
    (`migrations/env.py`). Pydantic `DocumentRecord` + ORM `Document` (FK `ON DELETE RESTRICT`).
  - `parsers/article.ArticleParser` (`article@1.0.0`; trafilatura + `strip_html` fallback) and the
    **nh3-based** `security/sanitization.strip_html` (text-only strongest allowlist + block-aware pass).
  - `worker/parse` (`run_parse_job`/`run_one_parse`/`reparse_raw_document`): network-free, scales out,
    no fetch lock; `storage.documents.upsert_document` last-write-wins on `canonical_url` (R2),
    preserving enrichment columns; content-hash + URL dedupe; `atlas parse <raw_id>`.
  - Adversarial review (B5 + deferred B4 SSRF) confirmed and **fixed 6 defects** with regressions
    ([ADR 0015](decisions/0015-b5-parse-pipeline-and-nh3-sanitizer.md)); SSRF IP classification
    independently re-audited (6to4/NAT64/IPv4-mapped/decimal-literal all correctly blocked).
  - New tests: sanitizer (+ adversarial regressions), article-parser golden, document upsert,
    full B4→B5 chain (+ same-GUID-edit and null-URL-collapse regressions). 194 pass total.
- **B6 — full-text search query path + `atlas search` CLI (2026-06-04).**
  - `atlascore.storage.search.search_documents` + `SearchHit`: FTS over `document` that matches the
    B5 `ix_document_fts` index expression exactly (`'simple'` config in lockstep), parses input with
    `websearch_to_tsquery` (robust to arbitrary input), ranks by `ts_rank`, returns a `ts_headline`
    snippet (`«…»`) + provenance, filters by source slug / `since` / `until`, and clamps to
    `MAX_LIMIT=100`. Static, fully parameterized SQL (filters via `CAST(:p AS …) IS NULL OR …`).
  - `atlas search "<query>" [--source][--since][--until][--limit]` CLI (registered in `cli/main.py`).
  - [ADR 0016](decisions/0016-b6-fts-query-path-and-search-contract.md) records the query contract
    that B7's MCP `search_documents` reuses. Tests: 1 integration (match/rank, filters, cap, snippet,
    provenance, `EXPLAIN`-seqscan-off index-usage regression) + 4 unit (CLI). 199 pass total.
- **B7 — MCP server + `search_documents` + `audit_log` table + result caps (2026-06-04).**
  - `audit_log` table (migration `0004`) + `Actor`/`RetentionClass` enums + ORM (no FKs,
    CHECK-bounded, `btree(ts)`/`btree(action)`); `storage.audit.write_audit_log` sanitizes `detail`
    via `sanitize_for_storage` at write time (§14).
  - `atlascore.mcp_server` (FastMCP, stdio) exposing `search_documents`: reuses B6's query path
    (extended `SearchHit` with `source_id`/`language`/`topics`/`entity_ids`/`ingested_at`/
    `parser_version`), sanitized snippet + provenance, **no raw HTML**, `limit ≤ 50`, audited per
    call. `atlas mcp dev` runs it over stdio.
  - [ADR 0017](decisions/0017-b7-mcp-server-search-documents-and-audit-log.md). Deviations from the
    illustrative spec sketch (single `source` slug; `topics`/`entity_ids` filters deferred to B11).
    Tests: 1 integration (cited results + sanitized audit + secret-redaction regression + caps) + 3
    unit; verified over real stdio with an MCP client. 203 pass total.
- **B8 — `source_health` + freshness + `get_source_health` + `atlas health` / `atlas digest`
  (2026-06-04).**
  - `source_health` table (migration `0005`) + `HealthStatus` enum + ORM (PK `source_id`, FK
    RESTRICT, `interval` lag, CHECK status, `btree(status)`).
  - `worker/freshness`: pure `classify_status` (`cooling_down` > `stale` > `warn` > `error` > `ok`,
    `COOLING_THRESHOLD=3`) + `compute_source_health` + `refresh_source_health` (idempotent upsert).
  - `get_source_health` MCP tool (read-only, secret-free, capped `≤200`, audited via
    `write_audit_log`); `storage.source_health.list_source_health` (secret-free view).
  - `atlas health` / `atlas digest` (control-plane, MCP-independent): recompute → read
    source_health + DLQ depth (`queue.dead_job_summary`) + fetch-worker liveness
    (`worker.run.fetch_worker_active` via `pg_locks`); digest delivers to stdout/log (B13 seam).
  - [ADR 0018](decisions/0018-b8-source-health-freshness-and-digest.md). Tests: 2 unit-files
    (classify matrix, digest render, CLI, offline tool) + 1 integration; verified live + stdio.
    219 pass total.
- **B9 — FRED connector + `time_series_point` + `get_macro_series` (2026-06-04).**
  - `time_series_point` (migration `0006`) — first **partitioned** table (RANGE on `ts`, composite
    PK `(id, ts)`, `UNIQUE NULLS NOT DISTINCT`, DEFAULT partition; raw SQL; drift-check excluded by
    name prefix). `storage.timeseries` (idempotent `upsert_observations` + `read_macro_series`).
  - `connectors.fred.series.FredConnector` + the connector ABC made multi-shape
    (`parse_entries`/`parse_observations` default-raise); `FetchContext` gains `params` +
    `credential`. `worker.macro` ingest; `run_one_fetch` dispatches RSS vs API by `access_method`.
  - `fetch.params` (`SourceFetch`/`SourceConfig`) for connector config; `security.secrets.
    resolve_credential` resolves the FRED key from env; the `?api_key=…` URL is sanitized out of
    logs/columns.
  - `get_macro_series` MCP tool (value-as-string, missing→null, capped `≤2000`, audited);
    `sources/fred_cpiaucsl.yaml`; `.env.example` FRED_API_KEY.
  - [ADR 0019](decisions/0019-b9-time-series-fred-connector-and-macro-series.md). Tests: unit (FRED
    parser golden + fetch contract via respx) + 1 integration (full chain, idempotent, audited,
    key-not-leaked). 228 pass total. Verified live + over real stdio.
- **B10 — SEC EDGAR + NVD document connectors (2026-06-04).**
  - `Connector.output_kind` (`document`|`timeseries`); `run_one_fetch` dispatches by output_kind
    (not access_method) so API sources can be documents (SEC/NVD) or time-series (FRED);
    `build_document_records` guards on it; `run_fetch_job` `FetchContext` gains `params`+`credential`.
  - `connectors.sec.submissions.SecSubmissionsConnector` (CIK submissions; UA + 10 req/s; no key) +
    `connectors.nvd.cves.NvdCveConnector` (CVE 2.0; keyless ~5/30s). Both `parse_entries` →
    `FeedEntry` → shared `ArticleParser` → searchable `document`. No new parser, **no migration**.
  - `sources/sec_edgar_apple.yaml` + `sources/nvd_recent_cves.yaml` (approved); fixtures + unit
    (SEC/NVD parser golden + fetch contract) + integration (search finds filing + CVE).
  - [ADR 0020](decisions/0020-b10-sec-nvd-document-connectors-and-output-kind-dispatch.md). 240 pass
    total; verified live; no new detect-secrets findings.

---

## In progress

- _(nothing in progress — soak-readiness hardening shipped; see the Done section below.)_

## Done — Soak-readiness hardening (pre-soak, 2026-06-06)

**Deploy glue + safety nets that make the §5 unattended-month soak safe — depth on existing
components, NOT a new source/service ([ADR 0034](decisions/0034-soak-readiness-hardening.md)).**

- **DB credential reconciliation.** `.env.example` now ships an explicit
  `POSTGRES_USER/POSTGRES_PASSWORD/POSTGRES_DB` block + a `DATABASE_URL` that agrees with the
  compose/CI role (`atlascore`, not `atlas`) — `cp .env.example .env && make dev && make migrate`
  works out of the box. `core.settings` default + integration `conftest._DEFAULT_URL` aligned;
  `.secrets.baseline` regenerated (pinned detect-secrets 1.5.0).
- **Dead-man's-switch.** Optional `HEARTBEAT_URL` (+ `HEARTBEAT_TIMEOUT`); `core.heartbeat.ping_heartbeat`
  (best-effort, never raises, never logs the token). `atlas scheduler run` pings it after each
  successful tick, so a sustained outage stops the heartbeat and an external monitor pages — closing
  the "silence = undetected outage" gap. `tests/unit/test_heartbeat.py`.
- **Cloud-embedding spend cap.** `storage.embeddings.upsert_embeddings` now meters paid cloud embeds
  on the **same `llm_spend` ledger + `LLM_MONTHLY_SPEND_CAP_EUR`** as summaries (`request_kind='embedding'`):
  at/over the cap a paid embed is skipped (existing vectors untouched, FTS still works); a successful
  paid embed records one ledger row. Closes the B21 gap where only summaries were metered. No schema
  change. `tests/integration/test_embedding_spend_cap.py` + cloud `is_paid` unit tests.
- **Vetted `infra/systemd/` units + runbook accuracy.** Shipped stack/scheduler/worker/`worker-proc@`/
  backup/digest unit+timer files (+ README). Runbook §2.3/§6C use `atlas scheduler run`/`atlas worker
  run` (no more crash-looping `apps.*`); backup timer → `infra/backup.sh` (offsite); stale
  `atlas jobs *`/`atlas restore verify`/`atlas enrich backfill` references replaced with the real
  commands (`atlas crawl dlq`, `infra/restore_drill.sh`, `atlas reindex-embeddings`) + break-glass SQL.
- **Compose `make dev` fixes (found by actually booting the stack + running the full day-1 path).**
  (a) `infra/docker-compose.yml` used a hard `${MEILI_MASTER_KEY:?…}` on the off-by-default Meilisearch
  service; Compose interpolates it **before** profile filtering, so `make dev` errored for anyone not
  using search → now `${MEILI_MASTER_KEY:-}`. (b) `Makefile` `COMPOSE` used `-f infra/...` with no
  project dir, anchoring it at `infra/` so it read `infra/.env` and **ignored the root `.env`** from
  `cp .env.example .env` → now `--project-directory . --env-file .env` (PR follow-up to ADR 0034).
- **Gates (verified on live PG16, not just unit):** ruff + ruff-format + mypy (148 src files) clean;
  migrations apply through `0012` and `alembic check` is **drift-free**; **full suite 406 passed, 1
  skipped (spacy)** on a real Postgres 16 + pgvector container — incl. the 2 new
  `test_embedding_spend_cap.py` integration tests (under-cap records a ledger row, over-cap skips the
  embed) and the compose stack booting cleanly. 7 new tests this change.

## Done — Post-MVP upgrade cycle (B16–B22)

- **Post-MVP upgrade cycle (B16–B22)** — completed by session (AI) 2026-06-05. A grounded, dependency-
  ordered roadmap (planned via a multi-agent design workflow). These are **depth on existing
  components**, not new sources/services, so the §5 freeze (which gates *new sources/services*) is
  respected. Each was an independently-committed block (code + migration + tests + docs + ADR + gates):
  - **B16** audit_log rotation + crawl_job prune (`atlas audit rotate`; no DDL) — ✅ **DONE**.
  - **B17** `time_series_point` yearly partitions + partition mgmt (migration 0010; `JobType.PARTITION`) — ✅ **DONE**.
  - **B18** alert maturity — watchlist gating (CVSS) + lifecycle CLI (`atlas alerts ack/resolve/list`) — ✅ **DONE**.
  - **B19** single-instance scheduler + continuous worker loop (wires B16/B17/B18; no DDL) — keystone — ✅ **DONE**.
  - **B20** connector depth — SafeFetcher headers + NVD keyed tier + conditional fetch (`source.checkpoint`, migration 0011) — ✅ **DONE**.
  - **B21** real embedding/LLM backends + spend-cap ledger + spaCy NER + `atlas reindex-embeddings` (migration 0012) — ✅ **DONE**.
  - **B22** SEC full-filing via sandboxed Tika + FilingParser (graceful metadata fallback; no DDL) — ✅ **DONE**.

---

## Next

**The B16–B22 post-MVP upgrade cycle is COMPLETE** (migrations 0010 (B17) / 0011 (B20) / 0012 (B21)
all applied; drift-free). Heavy/cloud paths (B21 sentence-transformers/cloud + B22 Tika/Java) keep the
deterministic offline default as the CI path and are contract-tested with respx + import guards. The
7-tool MCP surface stayed stable across the whole cycle.

**§5 freeze is now in force.** Per CLAUDE.md §5, **do not add a new source or service** until the
current set has run **unattended for a month**. The right next step is operational: deploy the B19
scheduler + worker (one of each) using the vetted `infra/systemd/` units (Linux/Hetzner) or
`infra/launchd/` agents (the current Mac-mini soak — `infra/launchd/install.sh`), enable a real
embedding/LLM/Tika backend if desired, and let it run.

**Current plan (2026-06-07):** the soak runs on a **Mac mini** (launchd; ultimately moving to a
Hetzner VPS), with **ntfy** for digest/alert delivery + a healthchecks.io dead-man's-switch. Run it
from a **separate pinned clone** (`~/atlascore-soak`) so ongoing V1 development in the main checkout
never touches the soak's DB/raw/code (see `infra/launchd/README.md` for the isolation model). V1 work
proceeds in branches during the soak and is NOT deployed into the soak instance until the month is up.

**V1 development (parallel track, lands on `main`, NOT in the pinned soak):**
- ✅ **ECB macro connector** (`connectors/ecb`, `sources/ecb_eur_usd.yaml`) — keyless ECB Data Portal
  CSV → `time_series_point` via the existing macro path; unit read inline from the CSV. Verified live
  (~7,083 EUR/USD points). First v1 increment.
- ✅ **arXiv connector** (`connectors/arxiv`, `sources/arxiv_cs_lg.yaml`) — subclasses `RssConnector`
  (arXiv API returns Atom → feedparser reused; only the query URL differs). Verified live (50 cs.LG
  papers ingested + searchable). Keyless. Second v1 increment.
- ✅ **GitHub releases connector** (`connectors/github`, `sources/github_ruff_releases.yaml`) —
  `/repos/{owner}/{repo}/releases` → documents; optional `GITHUB_TOKEN` (Bearer header) for the higher
  rate tier. Verified live (30 ruff releases ingested + searchable). Third v1 increment. (Advisories =
  documented follow-up.)
- ⏳ Last requested v1 increment: **read-only FastAPI dashboard** (new service + ADR), its own
  branch → PR → CI → merge; not enabled in the soak clone until the month completes.

**Pre-soak operator checklist (the remaining work is on the box, not in the repo):**
1. `cp .env.example .env`, set a real `POSTGRES_PASSWORD` (+ matching `DATABASE_URL` password) and a
   real `DEFAULT_USER_AGENT` (SEC EDGAR requires a contact).
2. **Configure + end-to-end TEST a real delivery channel** (ntfy is cheapest) — run `atlas digest` +
   `atlas alerts run` and confirm the message reaches your device. Do NOT soak on the silent
   `LogChannel` default.
3. **Set `HEARTBEAT_URL`** to an external monitor (e.g. healthchecks.io) with a ~5–10 min grace, so a
   dead box/scheduler pages instead of going silent.
4. **Prove backups restore on the real host:** `pg_dump` on PATH, `BACKUP_OFFSITE_REMOTE`/`_RSYNC` set,
   then run `infra/restore_drill.sh` once against the deploy's dumps.
5. Keep offline/free defaults for the soak (`EMBEDDING_BACKEND=local`, `LLM_BACKEND=none`,
   `TIKA_MODE=none`); if a cloud embedding backend is enabled it is now under the monthly cap (still
   set a provider-side billing cap as belt-and-suspenders).

The **embedding-spend ledger**, the **dead-man's-switch**, and the **deploy-glue/runbook** follow-ups
are now DONE (see the Done section above; [ADR 0034](decisions/0034-soak-readiness-hardening.md)).
Remaining candidate *depth* follow-ups (NOT new sources/services), only if a concrete need appears:
an `atlas crawl requeue` CLI (replace the break-glass requeue SQL), an `atlas summarize` pre-warm CLI,
per-filing second-fetch wiring of B22 extraction into the worker loop, a periodic restore-drill timer,
and a `source_health` history table. None are started.

---

## Known issues

- **Alert/digest delivery on a blank `ALERT_WEBHOOK_URL` — RESOLVED 2026-06-05** (found during a full
  end-to-end run). `get_delivery_channel` gated the webhook with `is not None`; the empty
  `ALERT_WEBHOOK_URL=` shipped in `.env.example` loads as `SecretStr('')`, so `cp .env.example .env`
  silently selected a `WebhookChannel` pointed at an empty URL (alert + digest delivery would fail on
  first deploy) instead of falling back to ntfy/SMTP/LogChannel. Now an empty webhook secret is treated
  as unconfigured, consistent with ntfy/SMTP. A shared `_clean()` helper strips + truthy-checks all
  three channels, so blank *and* whitespace-only values fall through (a real URL with stray surrounding
  whitespace is stripped, not rejected); regressions `test_empty_webhook_url_is_unconfigured` +
  `test_whitespace_only_values_are_unconfigured`.
- **Offline migration SQL (`alembic upgrade head --sql`) crashed on 0010 — RESOLVED 2026-06-05** (found
  during the same run). Migration `0010` (time-series yearly partitions) called
  `op.get_bind().exec_driver_sql(...)`, which has no live bind under offline mode's `MockConnection`, so
  the CI `migrations` "Offline SQL dry run" step would fail. Both introspection sites are now guarded
  with `op.get_context().as_sql`; the online path is unchanged (live DB already at head). New
  network-free regression `tests/unit/test_migration_offline_sql.py`. A sweep of migrations 0001–0012
  confirmed 0010 was the only such site.
- **B14 shared-raw erasure is deferred to the last reference (by design; ADR 0024).** One
  `raw_document` (a whole RSS feed / SEC submissions blob) backs **many** documents, and the FK is
  `RESTRICT`, so `atlas purge <doc>` deletes the raw file/row **only when no other document
  references it**. Until the last sibling is purged, the shared raw still holds the purged item's
  bytes among its siblings (honest limitation; the normalized personal data IS erased immediately).
- **B14 `atlas backup` needs `pg_dump` on PATH; the restore drill is a script, not a CI job.**
  `atlas backup` errors cleanly if `pg_dump` is absent (`infra/backup.sh` is the canonical
  operational path); `infra/restore_drill.sh` is run on demand / in the deploy runbook (it needs
  `pg_dump`/`pg_restore` + a scratch DB). The B14 restore was verified live against the throwaway
  PG16 container (14 tables + rows restored into a scratch DB).
- **Audit/crawl_job retention — RESOLVED in B16.** `atlas audit rotate` (`storage.retention`) deletes
  only `retention_class='routine'` audit rows beyond `AUDIT_ROUTINE_RETENTION` (purge rows retained
  forever, R14) and prunes terminal `crawl_job` rows beyond `CRAWL_JOB_RETENTION`. The B19 scheduler
  drives the daily cadence inline ([ADR 0027](decisions/0027-audit-rotation-and-crawl-job-retention.md)).
- **Alert watchlist + lifecycle — RESOLVED in B18.** `cve_watch`/`sec_filing` now fire only on
  watched/critical items when an active `watchlist.yaml` is present (CVSS via an NVD body marker;
  absent/inactive ⇒ fire-on-everything, backward compatible); `atlas alerts ack|resolve|list` drive
  the lifecycle ([ADR 0029](decisions/0029-b18-alert-watchlist-and-lifecycle.md)). Remaining (by
  design): `macro_release`/`freshness` carry evidence in `detail` (no backing document); the
  `freshness` dedupe is per `(source, status)`; delivery is fire-and-record (no backoff). Timed alert
  evaluation now runs inline in the B19 scheduler (`atlas scheduler run`) on the alert cadence.
- **Unattended operation — DELIVERED in B19.** `atlas scheduler run` (single-instance, advisory-locked)
  enqueues due fetches + runs freshness/alert/partition/audit inline; `atlas worker run` drains
  fetch→parse→enrich (fetch under the single-fetch lock). `atlas health`/`atlas digest` report real
  scheduler liveness ([ADR 0030](decisions/0030-b19-scheduler-and-worker-loop.md)). Deploy both as
  systemd units / compose services (one scheduler, one fetch worker) — see the runbook.
- **Real backends + spend cap — WIRED in B21** ([ADR 0032](decisions/0032-b21-real-backends-spend-cap-spacy-reindex.md)).
  `LLM_BACKEND=api` (`HostedSummaryBackend`, OpenAI/Anthropic/Google over httpx) and
  `EMBEDDING_BACKEND=sentence-transformers|openai|google` are now wired (import-guarded; respx-tested),
  with the **hard `LLM_MONTHLY_SPEND_CAP_EUR`** enforced via the `llm_spend` ledger
  (degrade-to-cache-only over cap) and cloud no-train/zero-retention controls. **Cloud *embeddings*
  are now ALSO under the same ledger + cap (`request_kind='embedding'`; skip-over-cap) — closed
  2026-06-06, [ADR 0034](decisions/0034-soak-readiness-hardening.md); previously only summaries were
  metered.** `NER_BACKEND=spacy`
  (`SpacyNer`) is wired with a rule-based fallback. `atlas reindex-embeddings` handles a model/dim
  switch (R8). The deterministic `local`/extractive/rule-based path remains the offline CI default;
  default `LLM_BACKEND=none` ⇒ `get_document_summary` stays cache-only.
- **`get_document_summary` writes a `summary` row on compute (documented exception).** docs/mcp §1
  says MCP "writes only audit_log", but §2.4/§4 specify compute-on-miss; the tool follows the
  specific contract. With the default `LLM_BACKEND=none` it is cache-only (no summary writes); a
  write happens only once a backend is configured and `allow_compute` is true. Documented in
  [ADR 0022](decisions/0022-b12-summaries-pipeline-and-content-hash-cache.md).
- **No `atlas summarize` CLI in MVP.** Summaries are produced via `get_document_summary`
  (`allow_compute=true` warms the cache); there is no control-plane summary command (not in
  CLAUDE.md §9). A pre-warm CLI is a possible later convenience.
- **B11 enrichment backends are deterministic baselines (by design; ADR 0021).** The `local`
  embedding backend is a **hashing vectorizer** (lexical overlap, not semantics) — a real ANN
  ranking needs the pluggable sentence-transformers/cloud backend wired (a localized change; same
  dim → no reindex). NER is **rule-based** (ticker/CIK + capitalized phrases), precision-over-recall:
  it drops single-word generic proper nouns, and corporate-suffix heuristics can misclassify ("Bank
  of England" → `company` via the `bank` suffix). `spacy`/`sentence-transformers` are declared
  optional extras but **not installed/wired** in MVP (`EMBEDDING_BACKEND=openai|google` raises a
  clear `ConfigError`). Language detection is the parser value + a tiny stopword heuristic, else
  honest `None`. `mention.sentiment` is always `null` (never fabricated). Entity **merging** is
  resolvable (`merged_into` is followed) but no merge **operation** exists yet — `merged_into` is
  null in practice.
- **B11 enrich runs via the queue OR `atlas enrich`, but no scheduler drives it yet.** The parse
  worker enqueues an idempotent `enrich` job per changed document; a worker must run `run_one_enrich`
  (or `atlas enrich <doc_id>` one-shot) to process it. The timed worker loop lands with the scheduler
  block. The `enrich` job's `dedupe_key` folds in `content_hash`, so an unchanged re-parse never
  re-enqueues and a changed body re-enriches (last-write-wins).
- **CI jobs that depend on later blocks are expected to be red until then**, by design: the
  `source-yaml` job calls `atlas source validate --all` (lands in **B2**), and `make seed` / the
  smoke target exercise ingestion (B2+). B1-owned jobs (lint-format, typecheck, test, migrations,
  secrets-scan, audit) and the `integration` job are green.
- **Local `make dev`/`make migrate` need Docker** (Postgres 16 + pgvector). Docker Desktop's
  first-boot on macOS can take a few minutes; the verification used a throwaway PG16 container.
- `make test` runs the integration tests only when a database is reachable (they skip otherwise);
  run `make dev && make migrate` first, or `make test-integration`, to exercise them locally.
- **`atlas crawl enqueue` is not idempotent across reruns** — the manual dedupe_key is timestamped
  (`fetch:<slug>:manual:<iso>`), so each invocation enqueues a fresh job (intended for a one-shot).
  The scheduler's per-tick dedupe_keys (B-later) are stable.
- **SSRF connection-pinning still deferred** (ADR 0014) — the guard validates all resolved IPs and
  re-validates every redirect hop, but the socket is not pinned to the validated IP, so a sub-second
  DNS-rebinding TOCTOU window remains. Accepted for a single-operator box; harden before any exposed
  deployment. (The B5 review independently re-confirmed the IP classification is correct for
  6to4/NAT64/IPv4-mapped/decimal-literal edges.)
- **nh3 `<head>` fragment edge** — `strip_html` (nh3) parses fragments in a body context, so bare text
  placed *directly* inside `<head>` can pass through as inert text (never active content). Not reachable
  via the B5 RSS path (feedparser hands the parser an extracted content fragment, no `<head>`).
- **Fetcher decoded-byte cap can overshoot by one decode chunk** — the streamed cap aborts as soon as
  the running decoded total exceeds `max_bytes`, so peak memory is bounded to ~cap + one chunk, not
  exactly the cap. Acceptable; a tighter raw-stream bound is a possible later hardening.
- **Null-URL document re-parse over-inserts** — a link-less item whose body changes on re-parse can't
  be matched to its prior row (dedupe is by content-hash incl. the GUID) and inserts a new row. Real
  feed items almost always carry a link (so they dedupe on `canonical_url`); documented in
  `storage/documents.py`.
- **Any feed change re-parses all of that feed's items** — the parse `dedupe_key` includes the whole
  raw feed's `content_hash`, so when any item changes, every item gets a fresh (idempotent, cheap)
  parse job. Correct, slightly wasteful; a per-item content key is a possible later refinement.
- **MCP `search_documents` is narrower than the docs/mcp §2.1 sketch (by design, B7)** — source
  filtering is a single `source` slug (not `source_ids[]`). The `topics`/`entity_ids` array-overlap
  filters are **now wired (B11)** once enrichment populates those columns. The `truncated` flag is a
  `len(hits) >= limit` heuristic (no separate count query), so it can read `true` when exactly
  `limit` rows exist with none behind them. Documented in
  [ADR 0017](decisions/0017-b7-mcp-server-search-documents-and-audit-log.md).
- **MCP server connects to the DB lazily (B7)** — `atlas mcp dev` starts the stdio server before
  reaching Postgres; the engine is created on the first tool call, so a DB outage surfaces as a tool
  error (`ToolError` to the client), not a startup failure. Intended for a single-operator box.
- **`atlas health` / `atlas digest` recompute `source_health` on each run (B8)** — there is no
  scheduler yet, so the CLI drives the freshness computation (compute → read) instead of only
  reading a job-populated table. Cheap at single-operator scale; when the scheduler block lands it
  runs the timed `job_type=freshness` job and the CLI can become a pure read. Documented in ADR 0018.
- **`source_health.duplicate_rate` is null in MVP (B8)** — per-item dedupe outcomes aren't recorded
  yet, so the column is honestly null rather than fabricated; populated when B13 adds the metric.
- **Digest delivery — RESOLVED in B13.** `atlas digest` now emits over the configured delivery
  channel (`ALERT_WEBHOOK_URL`/`NTFY_TOPIC`/SMTP, else stdout/log) and is still echoed locally; the
  freshness **alert** rule is also wired (B13). (Originally a B8 stdout-only seam.)
- **Scheduler-liveness half of the digest is a placeholder (B8)** — only fetch-worker presence is
  checked (via `pg_locks` on the fetch advisory lock); the single-scheduler liveness check lands
  with the scheduler block.
- **`time_series_point` yearly partitions — RESOLVED in B17.** Migration `0010` carves yearly
  `time_series_point_<year>` partitions (DEFAULT retained as catch-all); `atlas tsp partitions
  ensure` / `worker.partitions.run_partition_maintenance` (the B19 scheduler's monthly job) keep the
  next year ahead. Composite-PK + `NULLS NOT DISTINCT` inherited by children
  ([ADR 0028](decisions/0028-b17-time-series-yearly-partitions.md)).
- **FRED unit label + vintage are limited in MVP (B9)** — the human `unit` label is operator-supplied
  via `fetch.params.unit` because the observations endpoint omits it (the `/fred/series` metadata
  fetch is a follow-up); `kind` is `observation` only and `vintage` is null (market `bar`/OHLCV and
  ALFRED vintaged fetching are later). Documented in ADR 0019.
- **Macro parse runs inline in the fetch worker (B9)** — unlike the scaled-out RSS parse, a macro
  series is parsed + upserted inside the (rate-limited, single) fetch worker. Fine for small series;
  revisit if a high-volume time-series source is added.
- **NVD keyed tier + incremental fetch — RESOLVED in B20** ([ADR 0031](decisions/0031-b20-connector-depth-headers-keyed-nvd-checkpoint.md)).
  `SafeFetcher.extra_headers` (setdefault) enables the NVD keyed `apiKey`-header tier (~50 req/30s,
  `sources/nvd_recent_cves_keyed.yaml`; key redacted in logs/columns); NVD + SEC now fetch
  incrementally via the runtime-owned `source.checkpoint` cursor (NVD `lastMod` window, SEC
  newest-filing filter), re-fetch still idempotent (GUID+hash).
- **SEC full filing-text extraction — WIRED in B22** ([ADR 0033](decisions/0033-b22-sec-full-filing-sandboxed-tika.md)).
  `security.sandbox.tika` (out-of-process, RLIMIT/timeout/non-root, network-less, import-guarded) +
  `FilingParser` (`filing@1.0.0`) + `SecSubmissionsConnector.extract_filing` (opt-in via
  `fetch.params.extract_filings: 'true'` + `TIKA_MODE`) extract full filing text, with graceful
  metadata-only fallback when Tika is absent (the default — so CI/sandbox stay green without Java).
- **Local-only security-tooling quirks (do not reproduce in CI; NOT B5 regressions).**
  - `pip-audit` fails *locally* with an `ensurepip` SIGABRT (this sandbox blocks subprocess venv
    creation) — a local artifact, not a CI condition. The CI `audit` job now audits the locked
    **third-party** deps via `uv export --no-emit-project` (the unpublishable local `atlascore`
    package is excluded so `--strict` stays meaningful); it passes green.
  - A *newer local* `detect-secrets` flags the `hashed_secret` hex strings inside `.secrets.baseline`
    itself; CI's pinned version does not — the CI `secrets-scan` job passes green, and no B5 source
    file appears in any finding. The committed `.secrets.baseline` is unchanged from HEAD.

---

## Open questions

- ~~**Python import/packaging layout**~~ — **RESOLVED in B1** (ADR
  [0012](decisions/0012-packaging-layout-src-atlascore.md)): single `src/atlascore` import root.
- **OSS license at publish** — choose Apache-2.0 or MIT when the repo goes public
  (`LICENSE` is a proprietary placeholder while private).
- **Embedding backend + dim for the first deploy** — `EMBEDDING_BACKEND` = `local` |
  `openai` | `google`, with a fixed `EMBEDDING_DIM` per deployment (switching later is a
  documented backfill + reindex). The operator currently lacks a powerful machine, so a cloud
  backend is a first-class option; weigh that against the PII-egress note in the compliance docs.
- **`LLM_BACKEND` for summaries** — `local` | `api` | `none`. If `api`, set a hard
  `LLM_MONTHLY_SPEND_CAP_EUR` and prefer no-training / zero-retention / DPA settings; if `none`,
  `get_document_summary` returns only cached summaries (else `null` with a reason).

---

## Multi-worker / cross-session coordination protocol

Multiple workers and multiple human/AI sessions may touch this project over time. **This file is
the shared board.** To keep sessions continuing flawlessly:

1. **Before starting:** read [`../CLAUDE.md`](../CLAUDE.md) and this `STATUS.md`.
2. **Claim a work-area:** add a dated entry under **In progress** stating the area + intent + who
   (e.g. `- 2026-06-03 — B1 packaging layout — <name/agent>`). Do not start work in an area another
   session has already claimed.
3. **At close:** move your item to **Done**, update **Next** and **Known issues**, record any
   user-facing change in [`../CHANGELOG.md`](../CHANGELOG.md) `[Unreleased]`, add one ADR per
   architecture-changing decision, and keep diffs minimal and reversible.

Operational invariants worth remembering: exactly **one scheduler** instance; **one fetch worker**
in MVP (per-host token buckets; a second fetch worker is refused until the documented
`host_rate_state` scale-up path is enabled); parse/enrich may scale out via
`SELECT ... FOR UPDATE SKIP LOCKED`. The control plane (health, crawls, policy) is CLI-driven and
never depends on MCP.
