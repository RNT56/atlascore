# Changelog

All notable changes to AtlasCore are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

This is a private, internal-only project (FOSS boundary is code, not data). Until the
first tagged release, all work accumulates under **[Unreleased]**.

## [Unreleased] - 2026-06-03

This entry covers the planning + documentation scaffold, the **B1 skeleton** (the `atlascore`
package, the Alembic harness, and the `source` schema), the **B2 source registry** (canonical YAML,
default-deny, and the `atlas source` CLI), and the **B3 raw store + job queue** (content-addressed
raw storage, the `crawl_job` queue, and `sanitize_for_storage`), the **B4 RSS connector +
SSRF-guarded fetcher + worker**, the **B5 article parser + canonical `document` + parse
worker**, the **B6 full-text search query path + `atlas search` CLI**, the **B7 MCP server
+ `search_documents` + `audit_log` table**, the **B8 freshness/health subsystem
(`source_health` + `get_source_health` + `atlas health` / `atlas digest`)**, the **B9 macro
time-series (`time_series_point` + FRED connector + `get_macro_series`)**, the **B10 SEC EDGAR
+ NVD document connectors**, the **B11 enrichment subsystem (language → NER → topics →
embeddings + `entity`/`mention`/`embedding` + `get_entity_mentions`)**, the **B12 summaries
pipeline (`summary` table + content-hash cache + `get_document_summary`)**, the **B13 alert
subsystem (`alert` table + four rule-based alerts + delivery + `get_alerts`)**, the **B14
backups + restore drill + `atlas purge` (full R3 erasure cascade)**, and the **B15 hardening +
end-to-end demo + the final `get_raw_document` MCP tool**. The MVP (B1–B15) is complete with the
full 7-tool MCP surface. A **post-MVP upgrade cycle (B16–B22)** is now under way (depth on existing
components, not new sources/services).

### Added

- **V1 — read-only FastAPI dashboard API** (`atlascore.api`, behind the `web` extra; fourth v1
  increment, parallel-track, not in the soak — [ADR 0035](docs/decisions/0035-v1-read-only-fastapi-dashboard.md)).
  `create_app()` exposes `/healthz` + `/v1/search`, `/v1/alerts`, `/v1/sources/health`, reusing the
  existing `search_documents`/`list_alerts`/`list_source_health` query functions (one source of truth).
  Read-only, capped at `API_MAX_LIMIT=100`, returns provenance, never returns secrets/internal
  endpoints; no auth by design (loopback/VPN). The per-request session is a module-level `app.state`
  dependency (avoids a `from __future__ import annotations` closure-`Depends` 422). Unit (no-DB) +
  integration (live-DB, all three endpoints, asserts no secret leak) tests; run via
  `uvicorn atlascore.api.app:app`.

- **V1 — GitHub releases connector** (third v1 increment; parallel-track, not in the soak).
  `GithubReleasesConnector` fetches `/repos/{owner}/{repo}/releases` (JSON) → `document` rows (release
  id = GUID, `html_url` = link, name/tag = title, markdown `body` = body). An optional `GITHUB_TOKEN`
  (`credentials.ref`) is sent as an `Authorization: Bearer` header for the ~5,000/hour tier (unauth
  ~60/hour when unset; redacted by `sanitize_for_storage`). `sources/github_ruff_releases.yaml` +
  golden/contract tests + `GITHUB_TOKEN` in `.env.example`. **Verified live:** a 30-release crawl
  ingested 30 searchable documents. Security advisories are a documented follow-up; v1 ships releases.

- **V1 — arXiv connector** (second v1 increment; parallel-track, not in the soak). `ArxivConnector`
  **subclasses `RssConnector`** — the arXiv export API (`/api/query`) returns Atom, so feedparser
  parsing is reused verbatim and only the query-URL build differs (`fetch.params.search_query` +
  optional `max_results`/`start`/`sort_by`/`sort_order`). Each entry's `arxiv.org/abs/…` id is the
  GUID; the abstract is the document body. `sources/arxiv_cs_lg.yaml` (cs.LG) + golden/contract tests
  from a live fixture. **Verified live:** a `cat:cs.LG` crawl ingested 50 papers, searchable via
  `search_documents`. Keyless; no schema/worker change.

- **V1 — ECB macro connector** (first v1 increment; developed in parallel with the soak, NOT
  deployed into the pinned soak clone). `connectors/ecb/series.py:EcbConnector` fetches a series from
  the free, **keyless** ECB Data Portal (`/service/data/{flow}/{key}?format=csvdata`) and parses the
  CSV into `time_series_point` observations via the existing macro path (`output_kind=timeseries`, no
  worker/schema change). Unlike FRED, the unit is read inline from the CSV `UNIT` column; TIME_PERIOD
  parsing covers daily/monthly/quarterly/annual; missing values → `null`. `sources/ecb_eur_usd.yaml`
  (EUR/USD daily reference rate) + golden/contract tests from authentic fixtures. **Verified live:** a
  full-history crawl upserted ~7,083 points (`provider=ecb`, `unit=USD`). Powers `get_macro_series` +
  `macro_release` like FRED.

- **macOS launchd soak templates (`infra/launchd/`)** — the Mac-mini equivalent of `infra/systemd/`,
  for running the unattended soak on macOS before the Linux/Hetzner deployment. Self-locating wrapper
  scripts (`bin/soak-{scheduler,worker,digest,backup}.sh`) + four `.plist.template`s (scheduler/worker
  always-on with `KeepAlive`; digest/backup on a daily `StartCalendarInterval`) + `install.sh`/
  `uninstall.sh` + a README covering the soak↔dev isolation model. The backup wrapper robustly loads
  `.env` (tolerant of spaces/parens), maps `POSTGRES_PASSWORD`→`PGPASSWORD`, and prepends Homebrew
  paths so `pg_dump`/`rclone` resolve under launchd's bare PATH. Validated live on macOS: all plists
  `plutil`-lint clean, and the scheduler agent loads + runs `atlas scheduler run` under launchd
  (connects, ticks housekeeping, logs cleanly). Same single-scheduler/single-fetch invariants as systemd.

- **Soak-readiness hardening (pre-deploy)** ([ADR 0034](docs/decisions/0034-soak-readiness-hardening.md)).
  - **Dead-man's-switch:** optional `HEARTBEAT_URL` (+ `HEARTBEAT_TIMEOUT`); `core.heartbeat.ping_heartbeat`
    GETs it best-effort (never raises; logs only the exception type so the per-check token never leaks).
    `atlas scheduler run` pings it after each successful tick — a sustained outage stops the heartbeat so
    an external monitor (e.g. healthchecks.io) pages, instead of the operator having to notice silence.
  - **Cloud-embedding spend cap:** cloud embedding backends now expose `is_paid`/`provider`, and
    `storage.embeddings.upsert_embeddings` meters them on the **same `llm_spend` ledger + `LLM_MONTHLY_SPEND_CAP_EUR`**
    as summaries (`request_kind='embedding'`): at/over the cap a paid embed is **skipped** (existing
    vectors untouched — FTS still works) and a successful paid embed records one ledger row. Closes the
    B21 gap where only summaries were metered. Local/sentence-transformers backends are never charged.
    No schema change. New regressions: `tests/integration/test_embedding_spend_cap.py` + cloud-backend
    `is_paid` unit tests + `tests/unit/test_heartbeat.py`.
  - **Deploy glue:** vetted systemd unit/timer files under `infra/systemd/` (stack, scheduler, single
    worker, optional parse/enrich `worker-proc@`, backup→`infra/backup.sh`, digest) with full
    `[Install]`/`User`/`WorkingDirectory`/`Restart` + an install README.

- **B22 — SEC full-filing extraction via sandboxed Tika** ([ADR 0033](docs/decisions/0033-b22-sec-full-filing-sandboxed-tika.md)).
  - `atlascore.security.sandbox.tika` (`TikaClient` + `tika_available`): out-of-process,
    resource-bounded (`RLIMIT_AS`/`RLIMIT_CPU` + timeout, fixed argv, no shell, no network, non-root)
    Apache Tika — `subprocess` (`java -jar tika-app.jar --text`) or localhost `server` mode;
    `none` default. All heavy imports are lazy so it imports with no Java (`tika_available` → False).
  - `atlascore.parsers.filing.FilingParser` (`filing@1.0.0`): Tika text → control-char/whitespace
    cleaned `ParsedFiling` (Tika client dependency-injected for tests).
  - `SecSubmissionsConnector.extract_filing(...)`: opt-in via `fetch.params.extract_filings: 'true'`
    + `tika_available()`; fetches the primary filing doc through the same SSRF-guarded fetcher (PDF/HTML
    MIME allowlist) and extracts full text, else returns `None` and falls back to the B10 metadata
    body (a bad filing never fails the crawl; CI/sandbox stay green without Java). New `TIKA_*` settings.

- **B21 — real embedding/LLM backends + hard spend cap + spaCy NER + reindex** ([ADR 0032](docs/decisions/0032-b21-real-backends-spend-cap-spacy-reindex.md)).
  - Embeddings: `EMBEDDING_BACKEND=sentence-transformers` (real local, import-guarded) and
    `openai`/`google` cloud backends over httpx (respx-tested; vector dim validated == `EMBEDDING_DIM`,
    R8). `local` stays the deterministic hashing CI default.
  - Summaries: `LLM_BACKEND=api` → `HostedSummaryBackend` (OpenAI/Anthropic/Google over httpx,
    `LLM_PROVIDER`). Member bodies kept in an untrusted-evidence block; key header-only + redacted.
  - **Hard monthly spend cap:** new `llm_spend` ledger (migration `0012`) + `storage.llm_spend`
    (conservative price table). The summary pipeline checks month-to-date spend before any paid call
    and **degrades to cache-only** at/over `LLM_MONTHLY_SPEND_CAP_EUR` (null + reason); a successful
    paid compute records the ledger row in the summary's transaction. Free backends never charge.
  - NER: `NER_BACKEND=spacy` (`SpacyNer`, import-guarded) with rule-based fallback (never crashes).
  - `atlas reindex-embeddings [--model] [--batch] [--dry-run]` — bounded, resumable, idempotent
    embedding backfill/reindex for a model/dim switch (R8). New backend/cap/NER settings in `.env.example`.

- **B20 — connector depth: custom headers + NVD keyed tier + incremental checkpoint** ([ADR 0031](docs/decisions/0031-b20-connector-depth-headers-keyed-nvd-checkpoint.md)).
  - `SafeFetcher.fetch(..., extra_headers=)` merges caller headers via `setdefault` (re-applied per
    redirect hop) — they can never clobber the managed `User-Agent`/conditional cache headers.
  - **NVD keyed tier:** `NvdCveConnector` sends the resolved credential as the `apiKey` header
    (NIST ~50 req/30s) when `credential_ref` is set; `sources/nvd_recent_cves_keyed.yaml` (+ `NVD_API_KEY`)
    opts in. The key never reaches logs/columns (`sanitize_for_storage` redacts the dict + header-line
    forms; `_HEADER_RE` extended with `apikey`/`x-api-key`).
  - **Incremental fetch:** new nullable `source.checkpoint` jsonb (migration `0011`) — a runtime-owned
    cursor written only by the worker (never by the YAML loader, unlike `config`). `storage.checkpoints`
    + the `Connector.next_checkpoint`/`filter_entries` hooks (default no-ops) drive NVD server-side
    windowing (`lastModStartDate`/`lastModEndDate`) and SEC newest-filing filtering; the worker injects
    the cursor into `FetchContext` and persists it in the job txn. Re-fetch stays idempotent (GUID+hash).

- **B19 — single-instance scheduler + continuous worker loop** ([ADR 0030](docs/decisions/0030-b19-scheduler-and-worker-loop.md)).
  - `atlas scheduler run` (`scheduler.run`): holds a dedicated scheduler advisory lock (a second
    instance is refused, single-scheduler invariant §17), enqueues one **tick-bucketed** fetch job
    per due source (cron from `source.config['schedule']`, idempotent across overlapping ticks,
    default-deny), and runs **freshness/alert/partition/audit housekeeping inline** on independent
    cadences (system-wide tasks have no `source_id`, so they run inline rather than as queued jobs —
    no migration). `scheduler.cron` (`is_due`/`next_due_after`/`tick_bucket`) + `scheduler.lock`.
  - `atlas worker run [--role …]` (`worker.loop`): drains fetch→parse→enrich via the existing
    `run_one_*` claimers with a bounded idle backoff and periodic stale-claim reclaim; the **fetch**
    role holds the single-fetch lock (R16). Graceful SIGINT/SIGTERM shutdown; DI fetcher for tests.
  - `atlas health`/`atlas digest` now report **real scheduler liveness** (`scheduler_active`),
    replacing the B8 placeholder. New scheduler/worker cadence settings in `.env.example`.

- **B18 — alert maturity: watchlist gating + lifecycle** ([ADR 0029](docs/decisions/0029-b18-alert-watchlist-and-lifecycle.md)).
  - Git-tracked `watchlist.yaml` (`schemas/watchlist.py` + `storage/watchlist.py`, `WATCHLIST_PATH`):
    `cve_watch` fires only on watched/critical CVEs (CVSS ≥ floor / watched severity / cve_id /
    keyword — severity derived from the CVSS band) and `sec_filing` only on watched issuers (CIK,
    optional form). An absent/inactive watchlist preserves the pre-B18 fire-on-everything behaviour
    (`build_rules(watchlist)`; `ALL_RULES` stays ungated for tests). No migration, no DB table.
  - The NVD connector surfaces CVSS (v3.1→v3.0→v2) into the document body as a parser-safe marker
    (`CVSS base score <n> (<SEVERITY>).`) so the rule can gate on it without a schema change.
  - Alert lifecycle (`storage.alerts.acknowledge_alert`/`resolve_alert`, reusing the existing
    `status` column; `AlertTransitionError` on illegal/terminal moves) + `atlas alerts
    ack|resolve|list`. MCP `get_alerts` is unchanged (read-only).

- **B17 — time_series_point yearly partitions** ([ADR 0028](docs/decisions/0028-b17-time-series-yearly-partitions.md)).
  - Migration `0010` carves the deploy-time current + next calendar year into bounded
    `time_series_point_<year>` partitions via the detach-DEFAULT / create-bounded-child /
    move-matching-rows / re-attach-DEFAULT **with-rows dance** (DEFAULT retained as catch-all; parent
    composite PK + `NULLS NOT DISTINCT` natural key inherited), and widens the `crawl_job` job_type
    CHECK to accept the new `JobType.PARTITION`. Downgrades cleanly to the single-DEFAULT topology.
  - `atlascore.storage.partitions.ensure_yearly_partitions` (idempotent), `worker.partitions.
    run_partition_maintenance` (advisory-locked), and `atlas tsp partitions status|ensure`. Reads
    (`get_macro_series`) and writes (`upsert_observations`) route transparently across partitions.
    All `time_series_point*` children stay excluded from the drift-check by name prefix.

- **B16 — control-plane retention: `atlas audit rotate`** ([ADR 0027](docs/decisions/0027-audit-rotation-and-crawl-job-retention.md)).
  - `atlascore.storage.retention` (pure DML, no schema change): `rotate_audit_log` deletes only
    `retention_class='routine'` audit rows older than the window — filtered on `retention_class`,
    **never** action strings, so `purge` (erasure-proof) rows are structurally untouchable (R14);
    `prune_crawl_jobs` deletes only terminal (`done`/`dead`) crawl_job rows older than the window
    (keyed on `coalesce(finished_at, created_at)`), never live work; `run_retention` orchestrates
    both in one txn and writes **one retained** (`retention_class='purge'`) count-only bookkeeping
    entry. Idempotent; injected clock.
  - `atlas audit rotate [--dry-run] [--audit-window <Nd>] [--crawl-window <Nd>]` (control-plane).
    New `Settings.audit_routine_retention` (180d) / `crawl_job_retention` (90d) knobs in `.env.example`.
    The B19 scheduler will call `run_retention` on a daily cadence (no new `JobType`).

- **B15 polish — `get_raw_document` (the 7th MCP tool, completing the surface)** ([ADR 0026](docs/decisions/0026-b15-polish-get-raw-document.md)).
  - `atlascore.storage.raw_documents` + the `get_raw_document` MCP tool (docs/mcp §2.7, R7): resolve
    by `document_id` (preferred) or `raw_document_id`; **sanitized text by default**
    (`content_kind=sanitized_text`), or the **verbatim raw payload** with `include_raw_html=true`
    (`content_kind=raw_html`) wrapped in an untrusted-evidence notice. Byte-capped by `max_bytes`
    (1 KiB–1 MiB, default 256 KiB). Every call is audited; the raw-HTML path uses a distinct
    `action=mcp_raw_html_returned`. **No other tool ever returns raw HTML.** The MCP surface now
    ships **all 7 MVP tools**.

- **B15 — hardening + MVP completion** ([ADR 0025](docs/decisions/0025-b15-hardening-and-mvp-completion.md)).
  - **Security regression suite** (`make security` → `pytest -m security`): a new
    `tests/unit/test_security_regressions.py` (SSRF incl. DNS-rebinding, HTML active-content strip,
    secrets-never-in-DB-columns over the `crawl_job`/`audit_log` shapes, prompt-injection-as-evidence,
    and the read-only 6-tool MCP surface), plus the existing SSRF/sanitizer/secrets suites tagged
    `security`. The B14 purge multi-doc-summary regression rounds it out.
  - **End-to-end smoke/demo** (`make smoke` → `pytest -m smoke`): `tests/integration/test_smoke.py`
    runs source → fetch → parse → enrich → search → cited summary → alert → MCP `get_alerts` →
    backup → audited purge, unattended, with no live network (respx-mocked feed).
  - **README** rewritten to the as-built state (6 read MCP tools shipped, deterministic default
    backends, the deferred-past-MVP list); `make security` target added.

- **B14 — backups + restore drill + `atlas purge` erasure** ([ADR 0024](docs/decisions/0024-b14-purge-cascade-backups-and-restore-drill.md)).
  - **`atlas purge <doc_id|entity_id> --yes`** (`atlascore.storage.purge`): the audited right-to-
    erasure path (R3) — the single exception to raw-store immutability. A document purge runs the
    full cascade in one transaction: `embedding` → `mention` → `summary` (`document_id` **OR**
    `member_doc_ids[]` — the **multi-doc-summary** case) → `document` (drops it from FTS) →
    `raw_document` (file + row, **only when no other document references the shared raw**) → alert
    `evidence_doc_ids` reconciliation (an alert losing all evidence ⇒ `status='purged'`) → a
    **retained** `audit_log` entry (`retention_class='purge'`, content hashes + counts only, never
    personal data). An entity purge deletes the entity + its mentions and reconciles
    `document.entity_ids[]`; documents survive. Idempotent (a missing target is a clean no-op).
  - **`RawStore.delete`** — the single place a raw blob is removed (idempotent), reachable only from
    the purge path.
  - **`atlas backup`** (`atlascore.cli.backup`): an on-demand local snapshot (custom-format
    `pg_dump` + a copy of the content-addressed raw store) with the DB password passed via
    `PGPASSWORD`, never argv (§14). Offsite sync + retention + the nightly timer remain
    `infra/backup.sh`.
  - **`infra/restore_drill.sh`** — restores a dump into a throwaway scratch DB, runs sanity counts
    (incl. that `retention_class='purge'` audit rows survive a restore), and drops the scratch DB;
    never touches the live database. (Satisfies "test a restore once", SPEC B14.)

- **B13 — rule-based alerts + delivery + `get_alerts`** ([ADR 0023](docs/decisions/0023-b13-rule-based-alerts-delivery-and-get-alerts.md)).
  - **`alert` table** (migration `0009`, normal `op.create_table` — autogenerate/drift-checked):
    `alert_type`, `rule_id`, `severity`, `status (open|acknowledged|resolved|purged)`, `title`,
    `dedupe_key` (**unique**), `evidence_doc_ids text[]`, `detail` jsonb (sanitized before write),
    `delivered_at`/`delivery_status`, with `btree(status, severity, created_at)`.
  - **Four rule-based alerts** (`atlascore.alerts.rules`), idempotent on a deterministic
    `dedupe_key` (re-evaluation never double-fires): `sec_filing` + `cve_watch` (scan `document`s by
    source category, cite the doc in `evidence_doc_ids`), `macro_release` (latest FRED observation
    per series, evidence in `detail`), and `freshness` (stale/error/cooling_down `source_health` —
    closes the B8/§17 seam).
  - **Delivery channel** (`atlascore.alerts.delivery`): one configured channel —
    `ALERT_WEBHOOK_URL` → webhook, else `NTFY_TOPIC` → ntfy, else SMTP, else a stdout/log fallback.
    **This also wires `atlas digest`** (B8's stdout-only seam) — the digest now goes over the same
    channel.
  - **Alert worker + CLI** (`atlascore.worker.alerts`, `atlas alerts run`): evaluate rules
    (idempotent upsert) → deliver pending alerts (record `delivered_at`/`delivery_status`).
    Control-plane, MCP-independent.
  - **`get_alerts` MCP tool** (docs/mcp §2.6): read-only, capped (≤100), audited; filters by
    `alert_types`/`status` (default open+acknowledged)/`min_severity` (info<low<medium<high<critical)
    /`since`; resolves `evidence_doc_ids` to document/source provenance.

- **B12 — summaries pipeline + cited summaries** ([ADR 0022](docs/decisions/0022-b12-summaries-pipeline-and-content-hash-cache.md)).
  - **`summary` table** (migration `0008`, raw SQL): R4 content-hash cache keyed
    `unique(source_hash, model_name, model_version, prompt_version)` over the **ordered** member
    `content_hash`es; `scope (single|multi)`, `document_id` (null for multi), `member_doc_ids[]`,
    `citations` jsonb, `token_cost`, with `GIN(member_doc_ids)` for the R3 purge cascade. Excluded
    from the autogenerate drift-check by owning table.
  - **`SummaryBackend` + deterministic extractive `local` backend**
    (`enrichment.summaries.extractive.ExtractiveSummarizer`: lead-sentence extraction, per-claim
    citations to the **internal** doc id, offline/CI-safe). `LLM_BACKEND=none` → cache-only;
    `local` → extractive; `api` (hosted LLM) raises a clear `ConfigError` (pluggable behind the
    `api` extra, with the spend-cap / cloud-egress controls deferred to it).
  - **Summary pipeline** (`enrichment.summaries.pipeline.summarize_documents`): dedupe-before-
    summarize → `source_hash` → content-hash cache (compute-mode keyed to the active backend;
    cache-only mode = any cached summary for the content) → compute-on-miss + idempotent store.
    Never re-summarizes an identical `(content, model, prompt)` tuple; a re-parsed member busts the
    cache.
  - **`get_document_summary` MCP tool** (docs/mcp §2.4): `document_ids` (1–12, ordered),
    `allow_compute` (default true). Returns a cited summary (`cache=hit|miss`) with member-source
    provenance, or `summary_text=null` + a `reason` when `LLM_BACKEND=none`/`allow_compute=false`
    and nothing is cached (never a hallucination). Computes-on-miss only when a backend is
    configured; audited.
  - **`entity` / `mention` / `embedding` tables** (migration `0007`, raw SQL like `0006`):
    `entity` (trigram `gin (canonical_name gin_trgm_ops)` + array GINs + `merged_into`), `mention`
    (`btree(entity_id, mentioned_at)` + unique `(entity, document, model_name, model_version)`),
    `embedding` (`pgvector vector(384)` = `EMBEDDING_DIM`, HNSW ANN index, unique
    `(document, chunk_index, model_name, model_version)`, R8/R13). All three (and their objects) are
    excluded from the autogenerate drift-check by owning table in `migrations/env.py`.
  - **Deterministic, dependency-free default backends** (offline / CI-safe; heavy backends stay
    behind the existing `local-embeddings`/`cloud` extras): `enrichment.ner.rules.RuleBasedNer`
    (ticker/CIK patterns + sentence-bounded capitalized-phrase extraction, precision-over-recall),
    `enrichment.embeddings.hashing.HashingEmbeddingBackend` (BLAKE2b token bucketing, L2-normalized,
    dim from settings), `enrichment.topics` (keyword→topic map), `enrichment.language` (parser
    value + tiny heuristic, else honest `None`). `NerBackend`/`EmbeddingBackend` interfaces +
    factories make spaCy/sentence-transformers/cloud drop-in later (no schema change).
  - **Enrichment pipeline + worker** (`enrichment.pipeline.enrich_document`, `worker.enrich`):
    language → NER → topics → embeddings in one transaction; writes `document.language`/`topics`/
    `entity_ids`, find-or-create entities (by identifier, then exact name/alias), idempotent mentions
    (cleared + re-created per NER model+version), idempotent embeddings (R13 `source_hash` skip). The
    parse worker enqueues one idempotent `enrich` job per changed document, so fetch→parse→enrich
    runs unattended. `atlas enrich <doc_id>` runs it directly.
  - **`get_entity_mentions` MCP tool** (docs/mcp §2.3): resolve by `entity_id` → `identifier`
    (jsonb containment) → fuzzy `name` (trigram + aliases), `merged_into`-aware, with
    `match_confidence`; sanitized `context_snippet` + document/source provenance; capped (≤100),
    audited. **The deferred B7 `search_documents` `topics`/`entity_ids` filters are now wired**
    (array overlap, GIN-backed).

- **Canonical project plan** — `docs/PROJECT-PLAN.md`: rationale, architecture narrative,
  source categories, build-vs-adopt, the single ordered MVP backlog (B1–B15), roadmap,
  risks, and cost. Reconciles the previously conflicting build lists into one backlog
  (renamed from the legacy `AtlasWire-Personal-Plan.md`).
- **CLAUDE.md operating contract** — the agent entry point read first every session:
  identity, mission and boundaries, hard prohibitions, session start/close rituals,
  architecture contract, the 12 canonical tables, the 7-tool MCP surface, security and
  prompt-injection rules, honest compliance/PII posture, reliability and the
  multi-worker / cross-session coordination protocol, coding/testing standards, workflows,
  and definition of done.
- **Full `docs/` scaffold** — `docs/STATUS.md` (living progress board, seeded at B1 not-started),
  and deep-dive `README.md` files under `docs/architecture/`, `docs/connectors/`,
  `docs/schemas/`, `docs/security/`, `docs/compliance/`, `docs/mcp/`, and `docs/runbooks/`,
  each consistent with the canonical spec.
- **ADRs 0001–0011** — `docs/decisions/` with `0000-template.md` and a `README.md` index;
  Context/Decision/Consequences records (dated 2026-06-03, status Accepted) covering the
  authoritative schema and design resolutions: time-series uniqueness (NULLS NOT DISTINCT,
  PostgreSQL 15+), document re-parse versioning (current-state, reproducible-not-retained),
  audited purge vs immutable raw with full cascade, summary provenance/cache, host-scoped
  rate limiting and single fetch worker, structured retention shape, end-to-end freshness,
  single active embedding model+dim with backfill+reindex, pluggable embedding/LLM backends,
  SSRF/URL-safety policy, and secrets-in-storage redaction.
- **CI, pre-commit, and packaging configs** — `.github/workflows/ci.yml`
  (ruff format check, ruff lint, mypy, pytest with no live network, pip-audit, Alembic
  migration check, source-YAML schema validation, secrets scan; Python 3.12 matrix;
  optional Postgres 16 + pgvector integration job), `.github/dependabot.yml`
  (pip + github-actions, weekly), `.pre-commit-config.yaml` (ruff format+lint, light mypy,
  end-of-file-fixer, trailing-whitespace, detect-secrets, check-yaml, check-json), and
  `pyproject.toml` (uv-managed, locked deps, ruff/mypy/pytest config, `atlas` console script).
- **Folder skeleton** — the full canonical repository tree under `apps/`, `packages/`,
  `infra/`, `migrations/`, `tests/`, and `docs/` created as folders (with `.gitkeep`) so
  the structure physically exists; plus the root project files `README.md`, `CHANGELOG.md`,
  `CONTRIBUTING.md`, `SECURITY.md`, `LICENSE` (proprietary placeholder), `.gitignore`,
  and `.env.example`. The final Python import/distribution layout is finalized in B1.
- **B1 skeleton — application code begins.**
  - **`src/atlascore` package** (the import root): `core` (typed `Settings` with `SecretStr`
    secrets, sortable **UUIDv7** ids, UTC `Clock`, `structlog` logging, error hierarchy),
    `schemas` (`SourceRecord` + `AccessMethod`/`PolicyStatus` enums + default-deny
    `is_fetchable`), `storage.postgres` (`Base` with a stable naming convention + the `Source`
    ORM table), and the `atlas` Typer CLI (`atlas version` / `--version`). Empty placeholder
    subpackages preserve the future tree.
  - **Alembic harness** (`alembic.ini`, `migrations/env.py`, `migrations/script.py.mako`) and
    **migration `0001`**: creates the required `vector` (pgvector) and `pg_trgm` extensions, then
    the `source` table with its indexes, unique, and CHECK constraints. The DB URL is resolved
    from the environment (no secrets in the repo).
  - **Tests:** 26 unit (UUIDv7, settings + secret hygiene, source schema, source table shape, CLI)
    and 4 integration (migration applies, extensions present, server defaults + UUIDv7 PK, CHECK
    constraint enforcement) that **skip when no database is reachable** so `make test` is green offline.
  - **Locked dependencies** (`uv.lock`) and an audited **`.secrets.baseline`** (detect-secrets).

### Changed

- **Packaging layout finalized (ADR 0012):** a single **`src/atlascore`** import root replaces the
  empty top-level `apps/` and `packages/` placeholders; the `packages` (libraries) vs `apps`
  (runnable roles) distinction is preserved as subpackage names. `pyproject.toml` hatchling/ruff/
  mypy/coverage retargeted to `src`; `uv run mypy` checks the `atlascore` package; pre-commit mypy
  scans `^src/`. CLAUDE.md §7 (canonical tree + packaging note) and the ADR index updated to match.
- **`alembic.ini`:** added `path_separator = os` (modern config; silences Alembic's legacy-splitting
  deprecation that `filterwarnings=error` would otherwise turn into a test error).

### Fixed

- **Deploy-glue + runbook accuracy (pre-soak, [ADR 0034](docs/decisions/0034-soak-readiness-hardening.md)).**
  - `.env.example` now ships a `POSTGRES_USER/POSTGRES_PASSWORD/POSTGRES_DB` block and a `DATABASE_URL`
    that **agrees with the compose/CI Postgres role** (`atlascore`, not `atlas`) — previously a
    `cp .env.example .env && make dev && make migrate` failed on auth. `core.settings` default and the
    integration `conftest._DEFAULT_URL` are aligned to the same role; `.secrets.baseline` regenerated.
  - `infra/docker-compose.yml`: the optional Meilisearch service used a hard `${MEILI_MASTER_KEY:?…}`,
    which Compose evaluates during interpolation **before** profile filtering — so a plain
    `docker compose up -d` / `make dev` errored out for anyone not using the (off-by-default) `search`
    profile. Changed to `${MEILI_MASTER_KEY:-}`; the key requirement now applies only when the profile
    is actually enabled (Meilisearch enforces it at its own startup).
  - `Makefile`: the `COMPOSE` command used `-f infra/docker-compose.yml` without a project directory,
    so Compose anchored the project dir at `infra/` and read `infra/.env` — **ignoring the root `.env`**
    that `cp .env.example .env` / `make setup` create, breaking `make dev` with "POSTGRES_PASSWORD
    missing." Now `--project-directory . --env-file .env`, so `make dev`/`make down` read the repo-root
    `.env`. (Found by running the full local day-1 path end-to-end.)
  - Runbook: the §2.3 systemd units now use `atlas scheduler run` / `atlas worker run` (the crash-looping
    `python -m apps.scheduler` / `apps.worker --role process` blocks are gone) and point at the shipped
    `infra/systemd/` files; the backup timer invokes `infra/backup.sh` (offsite) not `atlas backup`
    (local-only); and the non-existent `atlas jobs requeue/stuck/reclaim`, `atlas restore verify`, and
    `atlas enrich backfill` references are replaced with the real commands (`atlas crawl dlq`,
    `infra/restore_drill.sh`, `atlas reindex-embeddings`) + break-glass SQL.

- **Alert/digest delivery selected a broken webhook on a blank `ALERT_WEBHOOK_URL`** (found during a
  full end-to-end run). `get_delivery_channel` gated the webhook with `is not None`, but the empty
  `ALERT_WEBHOOK_URL=` shipped in `.env.example` loads as `SecretStr('')` — so `cp .env.example .env`
  produced a `WebhookChannel` pointed at an empty URL, silently breaking alert + digest delivery on
  first deploy instead of falling back to ntfy/SMTP/LogChannel. An empty webhook secret is now treated
  as unconfigured, consistent with the ntfy/SMTP branches. A shared `_clean()` helper now strips and
  truthy-checks **all three** channels, so blank *and* whitespace-only values fall through (and a real
  webhook URL with stray surrounding whitespace is stripped, not rejected). Regressions
  `test_empty_webhook_url_is_unconfigured` + `test_whitespace_only_values_are_unconfigured`.
- **`alembic upgrade head --sql` (offline SQL generation, a CI `migrations` step) crashed** on
  migration `0010` (time-series yearly partitions), which called `op.get_bind().exec_driver_sql(...)`
  — there is no live bind under offline mode's `MockConnection`. Both introspection sites are now
  guarded with `op.get_context().as_sql` (emit the partition DDL unconditionally on the offline/fresh-DB
  path; the downgrade helper emits nothing for the collapse loop offline). The online path is
  unchanged. New network-free regression `tests/unit/test_migration_offline_sql.py` renders the whole
  chain offline. (A migration sweep confirmed `0010` was the only such site.)
- **CI `secrets-scan`** used a non-existent `detect-secrets --fail-on-unaudited` flag (not in
  detect-secrets 1.5.0); replaced with a baseline re-scan + an explicit gate that fails on any
  unaudited or real finding.
- **CI `integration` job** failed on every push to `main` with pytest exit code 5 (no tests
  collected) because `tests/integration/` was empty; it now collects real DB tests (which skip
  cleanly when no database is present).

### Added — B2 (source registry)

- **Canonical source-YAML loader + validation** — `atlascore.schemas.source.SourceYAML` (nested
  `policy`/`credentials`/`fetch`) with `to_record()` mirroring the YAML losslessly into a `source`
  row (R9: the logical slug round-trips in `config.slug`; `credentials.ref` → `credential_ref`,
  an env-key name, never a value). `atlascore.core.durations` parses the retention/freshness
  duration grammar (`Nh|Nd|Nw|Nm|Ny|permanent`; retention allows `permanent`, freshness does not).
- **Default-deny policy gate** — `allowed_uses` is required for `approved`/`restricted`; validation
  refuses `enabled: true` on a non-fetchable policy; `is_fetchable = enabled AND policy.is_fetchable`.
  `unknown`/`pending`/`prohibited` are refused at validate time and at the fetchability gate.
- **`atlas source` CLI** — `validate` (pure, no DB; backs the CI `source-yaml` job), `sync`
  (validate + idempotent upsert into the `source` table, keyed on `name`), `test` (a no-network
  policy dry-run; live reachability lands with the connector in B4), and `list`.
- **`sources/` registry** — the git-tracked source of truth, with `sources/README.md` and an
  approved public-domain RSS source (`fed_press_releases`). `tests/fixtures/sources/` covers the
  approved, refusal (`pending`/`unknown`/`prohibited` + enabled), and malformed cases.
- **ADR [0013](docs/decisions/0013-source-registry-on-disk-and-slug-round-trip.md)** — registry
  location, the slug↔UUID reconciliation, and the `sync`/`list` CLI additions. CLAUDE.md §7
  (`sources/`) and §9 (`source sync`/`list`) and docs/connectors §4.6 updated. `make seed` now
  validates + registers the registry.

### Added — B3 (raw store + job queue)

- **Immutable raw store** — `atlascore.storage.rawstore.RawStore`: gzipped, content-addressed
  blobs on local disk (`<algo>/<shard>/<rest>.gz`), atomic writes, idempotent dedupe on the content
  hash. `atlascore.core.hashing.content_hash` (`"<algo>:<hexdigest>"`).
- **Postgres job queue** (migration `0002`, `crawl_job`) — `atlascore.storage.queue`: `enqueue`
  (idempotent on `dedupe_key`), `claim_next` (`SELECT … FOR UPDATE SKIP LOCKED`), `complete`,
  `fail` (retry → `pending` with exponential backoff, then → `dead` over `max_attempts`),
  `reclaim_stale` (visibility-timeout crash safety), and `dead_jobs` (the DLQ). `now` is injected
  for deterministic retry timing.
- **`raw_document` table** (migration `0002`) — content-addressed index row (`content_hash`
  unique), FK to `source`/`crawl_job` with `ON DELETE RESTRICT`.
- **`sanitize_for_storage`** — `atlascore.security.secrets`: the single write-time redactor for
  `crawl_job.last_error`/`payload` (and later `audit_log.detail`). Strips URL query/userinfo,
  redacts `Authorization`/`Cookie` headers, bearer tokens, and secret-keyed values recursively
  (CLAUDE.md §14). Routed through `enqueue`/`fail`.
- **`atlas crawl` CLI** — `enqueue` (enqueue a fetch job for a fetchable source; default-deny
  enforced) and `dlq` (inspect dead jobs). Shared `cli/_db.py` session helper.

### Added — B4 (RSS connector + SSRF-guarded fetcher + worker)

- **SSRF / URL-safety guard** — `atlascore.security.url_safety`: scheme allowlist (`http`/`https`),
  resolve-and-validate-all-IPs (reject loopback/RFC1918/CGNAT/link-local/ULA/IPv4-mapped/metadata and
  anything not globally routable), and per-redirect re-validation. Connection-pinning to the validated
  IP is a documented follow-up — [ADR 0014](docs/decisions/0014-ssrf-validate-all-ips-pin-deferred.md).
- **Connector SDK + fetcher** — `atlascore.connectors_sdk`: the `Connector` interface, `FetchContext`,
  a per-host `HostRateLimiter`, and `SafeFetcher` (an SSRF-guarded `httpx` fetcher with ETag/
  Last-Modified conditional requests, manual capped redirect handling, MIME allowlist, and a size cap).
- **RSS connector** — `atlascore.connectors.rss.RssConnector` (feedparser; stable GUID extraction;
  fails loudly only on non-feed content).
- **Fetch worker** — `atlascore.worker`: `run_fetch_job`/`run_one_fetch` drive fetch → store raw before
  parse → enqueue one `parse` job per GUID (idempotent, so re-fetch ignores seen GUIDs) → checkpoint
  via stored ETag/Last-Modified; claim and process run in separate transactions. The **single-fetch-worker
  startup guard (R16)** is a Postgres advisory lock. `atlas crawl once` enqueues + runs one fetch.

### Added — B5 (article parser + canonical `document` + parse worker)

- **`document` table** (migration `0003`) — the current-state canonical record (docs/schemas §2.4):
  Pydantic `DocumentRecord` + ORM `Document`. Plain btree indexes are model-declared
  (autogenerate-managed); the partial-unique `uq_document_canonical_url` (`WHERE canonical_url IS
  NOT NULL`), the FTS `ix_document_fts` (`gin(to_tsvector('simple', …))`), and the array GINs
  `ix_document_topics`/`ix_document_entity_ids` are created via raw SQL and excluded from the
  autogenerate drift-check by `_include_object` in `migrations/env.py`. FKs use `ON DELETE RESTRICT`
  (R17).
- **Article parser** — `atlascore.parsers.article.ArticleParser` (pinned `parser_version`
  `article@1.0.0`): trafilatura primary + an HTML-sanitizer fallback that does the work for RSS
  summaries → sanitized `body_text` + title + canonical_url + published_at. Golden output pinned.
- **HTML sanitizer** — `atlascore.security.sanitization.strip_html`: a text-only (strongest
  allowlist) sanitizer built on **nh3** (a real HTML5 tokenizer) with a block-aware stdlib second
  pass; drops the contents of active-content elements and neutralizes `javascript:`/`on*`/`<iframe>`
  by construction.
- **Parse worker** — `atlascore.worker.parse`: `run_parse_job`/`run_one_parse` consume the per-GUID
  parse jobs B4 enqueues (network-free, may scale out, no fetch lock); `reparse_raw_document` backs
  `atlas parse <raw_id>`. `upsert_document` is last-write-wins on `canonical_url` (R2), preserving
  enrichment-owned columns; content-hash dedupe makes replays/re-parses idempotent.
- **`atlas parse <raw_id>`** CLI — (re)parse one immutable raw document into canonical documents.
- **Tests** — sanitizer (incl. adversarial regressions), article-parser golden, document upsert
  semantics, and the full B4→B5 chain integration (fetch → raw → parse → documents → FTS → idempotent
  re-parse) against Postgres 16.

### Fixed / Security — B5 adversarial review

A multi-agent adversarial review (each finding independently refuted by a skeptic) drove these fixes:

- **Sanitizer (high):** the previous hand-rolled `html.parser` + depth-counter `strip_html` leaked
  active content on adversarial markup (an unterminated attribute quote in a `<script>` start tag
  was mis-tokenized as text; a stray drop-tag close zeroed a shared counter held by another open
  element). Replaced with **nh3** (real HTML5 tokenizer) + a per-element drop **stack**; added
  regressions for both attack classes.
- **Fetcher (medium):** the response size cap was applied only *after* the full body was buffered
  and auto-decompressed, so a decompression bomb defeated the documented "runaway body" guard. The
  body is now **streamed** with an incremental decoded-byte cap (mid-stream abort), a `Content-Length`
  early reject, and a pre-stream MIME check.
- **Parse (medium):** a same-GUID in-place feed edit was stranded (the parse `dedupe_key` was
  GUID-only, so the new raw was never re-parsed). The key now includes the raw `content_hash`,
  re-enqueuing a parse job on a real change (R2).
- **Parse (medium):** the null-`canonical_url` dedupe could collapse two distinct items with an
  identical title+body. The content-hash fingerprint now folds in the feed GUID; the documented
  limitation was corrected to disclose the real (over-insert) direction.
- **Compliance (medium):** the feed `<title>` was stored verbatim while only the body was
  sanitized. The title is now run through `strip_html` before persisting.

### Changed — B5

- **New runtime dependency `nh3`** (ammonia HTML5 sanitizer) — the security boundary for parsed
  body/title text ([ADR 0015](docs/decisions/0015-b5-parse-pipeline-and-nh3-sanitizer.md)).

### Fixed — CI (first-ever run on push to GitHub)

The repository was pushed to GitHub for the first time; the first CI run surfaced two latent
workflow-config bugs from B1 (never exercised while the repo was empty), both fixed:

- **Workflow failed to compile** — the integration job referenced `${{ runner.temp }}` in a
  job-level `env:` block, where the `runner` context is unavailable, so GitHub rejected the entire
  workflow (0 jobs ran). Switched to `${{ github.workspace }}` (valid at job-env scope).
- **`pip-audit --strict` failed** on the unpublishable local `atlascore` package — now audits the
  locked **third-party** deps via `uv export --no-emit-project`, keeping `--strict` meaningful.

CI is green on all 8 jobs (lint-format, typecheck, test, audit, migrations, source-yaml,
secrets-scan, integration).

### Added — B6 (full-text search query path + `atlas search` CLI)

- **`atlas search "<query>"`** — full-text search over canonical documents:
  `[--source <slug>] [--since <date>] [--until <date>] [--limit N]`. Prints ranked, **sanitized**
  `body_text` snippets (matched terms wrapped in `«…»`) with provenance — source, canonical URL,
  published date, document id, and rank. `--since`/`--until` are UTC date bounds (since inclusive,
  until exclusive); `--limit` defaults to 20 and clamps to 100.
- **`atlascore.storage.search.search_documents`** — the FTS query over `document`: matches the
  `ix_document_fts` GIN index (B5 migration `0003`) using the *identical* `to_tsvector('simple',
  coalesce(body_text, ''))` expression so the index is used; parses input with
  `websearch_to_tsquery('simple', …)` (robust to arbitrary input; supports quoted phrases / `or` /
  `-term`); ranks by `ts_rank`; returns a capped, provenance-bearing `SearchHit` list. The SQL is
  static and fully parameterized (optional filters are `CAST(:p AS …) IS NULL OR …` guards) — no
  injection surface ([ADR 0016](docs/decisions/0016-b6-fts-query-path-and-search-contract.md)).
- **Tests** — an integration test (text match + ranking, source/since/until filters, result cap,
  snippet + provenance, and an `EXPLAIN`-with-`enable_seqscan=off` regression asserting
  `ix_document_fts` is used) and unit tests for the CLI (help, clean exit without a DB, malformed
  `--since` rejection, UTC date normalization).

### Added — B7 (MCP server + `search_documents` + `audit_log` table)

- **`audit_log` table** (migration `0004`) — the append-only audit trail (docs/schemas §2.11):
  `Actor`/`RetentionClass` enums, an ORM model with CHECK constraints and `btree(ts)`/`btree(action)`
  indexes, and **no foreign keys** by design (the log must outlive purge of the `source_id`/
  `target_ref` it references; `target_ref` is text). `retention_class='purge'` entries are retained;
  `routine` rotates (R14).
- **`atlascore.storage.audit.write_audit_log`** — the single sanitized audit write path: `detail`
  passes through `sanitize_for_storage` before it reaches the jsonb column, so no secret can enter
  the audit trail or its backups (§14).
- **MCP server** (`atlascore.mcp_server`, FastMCP over **stdio**) exposing **`search_documents`** —
  reuses the B6 query path (now extended with provenance columns: `source_id`, `language`, `topics`,
  `entity_ids`, `ingested_at`, `parser_version`), returns ranked **sanitized** snippets + a
  provenance bundle (never raw HTML, §13), caps `limit ≤ 50` (§2.1), and writes one sanitized
  `audit_log` row per call (`actor=mcp`, `action=mcp_tool_call`). Input is schema-validated;
  malformed/oversized input is rejected before any DB work
  ([ADR 0017](docs/decisions/0017-b7-mcp-server-search-documents-and-audit-log.md)).
- **`atlas mcp dev`** — run the read-only MCP server over stdio for an MCP client / agent host.
- **Tests** — an integration test (tool returns cited results; every call audited + sanitized; a
  secret embedded in the query is redacted in `audit_log.detail`; caps + validation hold) and unit
  tests (CLI help, offline server build + tool contract, hit→payload mapping). Verified end-to-end
  over real stdio with an MCP client driving `atlas mcp dev`.

### Added — B9 (macro time-series: `time_series_point` + FRED connector + `get_macro_series`)

- **`time_series_point` table** (migration `0006`) — the first **RANGE-partitioned** table
  (docs/schemas §2.7): composite PK `(id, ts)`, `UNIQUE NULLS NOT DISTINCT (provider, series_id,
  ts, vintage)`, a `kind` CHECK, OHLCV columns (nullable), and a DEFAULT partition. Created via raw
  SQL and excluded from the autogenerate drift-check by a `time_series_point` name-prefix rule.
- **FRED connector** (`atlascore.connectors.fred.series.FredConnector`) — fetches a series'
  observations from the free/official FRED API and parses them to `Decimal` values (a FRED `"."`
  becomes null, never zero-filled). The connector ABC now supports both document and time-series
  families: `parse_entries` / `parse_observations` default to `NotImplementedError`; `FetchContext`
  carries connector `params` + the resolved `credential`.
- **Macro ingest** — the single fetch worker (`run_one_fetch`) dispatches on `access_method`:
  RSS → documents, **API → `worker.macro`** (fetch → store raw → `storage.timeseries.
  upsert_observations`, idempotent on the natural key). `atlas crawl once <fred_source>` works.
- **`params` on the source registry** — `fetch.params` (`dict[str,str]`) carries non-secret
  connector config (`series_id`, optional `unit`), round-tripping into `source.config`.
  **`resolve_credential`** resolves a `credential_ref` to its env value at runtime; the FRED
  `?api_key=…` URL is sanitized before any log/column (the key never reaches `crawl_job`).
- **`get_macro_series`** MCP tool (third tool) — a series + ordered observations; each `value` is a
  **string** (preserves `numeric` precision), missing values null, capped (`limit ≤ 2000`), audited;
  `found=false` + reason for an un-ingested series
  ([ADR 0019](docs/decisions/0019-b9-time-series-fred-connector-and-macro-series.md)).
- **FRED source** `sources/fred_cpiaucsl.yaml` (CPI, approved); fixtures + tests — unit (parser
  golden incl. missing values, fetch contract via respx incl. api-key URL, error cases) and
  integration (full fetch → `time_series_point` → idempotent re-fetch → `get_macro_series`, audited,
  api-key-not-leaked). Verified live + over real stdio.

### Added — B10 (SEC EDGAR + NVD document connectors)

- **`Connector.output_kind` dispatch** — a `document | timeseries` discriminator on the connector;
  the single fetch worker dispatches by what the connector *produces* (not `access_method`), so an
  `api` source can be a document source (SEC, NVD) or a time-series source (FRED). `run_fetch_job`'s
  `FetchContext` now carries connector `params` + the resolved `credential`
  ([ADR 0020](docs/decisions/0020-b10-sec-nvd-document-connectors-and-output-kind-dispatch.md)).
- **SEC EDGAR connector** (`atlascore.connectors.sec.submissions.SecSubmissionsConnector`) — fetches
  `/submissions/CIK##########.json` for `params.cik` (zero-padded), sends the required contact
  `User-Agent` + 10 req/s; each recent filing becomes a searchable `document` (accession = GUID,
  primary-doc URL = canonical URL, `form — company` = title). No API key.
- **NVD connector** (`atlascore.connectors.nvd.cves.NvdCveConnector`) — fetches the CVE 2.0 JSON API
  (keyless tier, ~5 req/30 s); each CVE becomes a searchable `document` (CVE id = GUID, NVD detail
  URL = canonical URL, English description = body). Optional `params.keyword` / `results_per_page`.
- **Reuses the B5 document pipeline** — both connectors implement `parse_entries`; the shared
  `ArticleParser` produces the sanitized body. No new parser, **no schema change**.
- **Sources** `sources/sec_edgar_apple.yaml` + `sources/nvd_recent_cves.yaml` (both `approved`);
  fixtures + tests — unit (SEC/NVD parser golden, fetch contract via respx) and integration (full
  fetch → parse → `search_documents` finds the filing + the CVE — the B10 done-when).

### Added — B8 (freshness/health: `source_health` + `get_source_health` + digest)

- **`source_health` table** (migration `0005`) — the per-source freshness/health snapshot
  (docs/schemas §2.12): `HealthStatus` enum + ORM (PK `source_id`, FK RESTRICT, `interval`
  `freshness_lag`, CHECK-bounded `status`, `btree(status)`). Current snapshot only.
- **Freshness computation** (`atlascore.worker.freshness`) — `refresh_source_health` computes (over
  `crawl_job` + `document`) and idempotently upserts each source's health: `freshness_lag = now -
  coalesce(last_item_published_at, last_success_at)`, classified by the pure, unit-tested
  `classify_status` (precedence `cooling_down` > `stale` > `warn` > `error` > `ok`;
  `COOLING_THRESHOLD = 3` consecutive failed fetches). Freshness-critical fields are exact; quality
  metrics are simple/honest; `duplicate_rate` is null in MVP.
- **`atlas health`** — on-demand snapshot (recompute → `source_health` + `crawl_job` DLQ depth +
  fetch-worker liveness via `pg_locks`). **`atlas digest`** — builds the daily summary (non-ok
  sources + DLQ + liveness) and emits it; B8 delivers to stdout/log (a seam — B13 wires the
  webhook/ntfy/email channel). Both are control-plane and **MCP-independent**.
- **`get_source_health`** MCP tool — read-only, **secret-free** mirror (never `base_url`/
  `credential_ref`/hostnames), filterable by slug/status, capped (`limit ≤ 200`), audited per call
  ([ADR 0018](docs/decisions/0018-b8-source-health-freshness-and-digest.md)).
- **Tests** — unit (the `classify_status` matrix; `build_digest` / `_fmt_lag`; CLI help; offline
  tool registration) and integration (stale/fresh/failing classification; `get_source_health`
  audited + secret-free; `atlas digest` surfaces a stale source). Verified live + over real stdio.

[Unreleased]: https://example.invalid/atlascore/compare/HEAD
