# AtlasCore — STATUS (living board)

> **This is the shared coordination board for every session (human or AI) and every worker.**
> Read [`../CLAUDE.md`](../CLAUDE.md) and this file before starting. Update it at session close.
> Last updated: **2026-06-03**.

---

## Current phase

**Block B8 — DONE (2026-06-04). Next: B9.**

The **freshness/health subsystem** is in place. Migration `0005` creates `source_health`
(docs/schemas §2.12): current snapshot, PK `source_id` (FK RESTRICT), `interval` `freshness_lag`,
CHECK-bounded `status`, `btree(status)`. `worker/freshness.refresh_source_health` computes
(read-only over `crawl_job` + `document`) + idempotently upserts each source's health;
`freshness_lag = now - coalesce(last_item_published_at, last_success_at)`, classified by the pure,
unit-tested `classify_status` (`cooling_down` > `stale` > `warn` > `error` > `ok`;
`COOLING_THRESHOLD=3`). `atlas health` / `atlas digest` (control-plane, MCP-independent) recompute
then read source_health + `crawl_job` DLQ depth + fetch-worker liveness; the digest delivers to
stdout/log (a seam — B13 wires the real channel). The **`get_source_health`** MCP tool is a
read-only, **secret-free**, audited, capped (`≤200`) mirror
([ADR 0018](decisions/0018-b8-source-health-freshness-and-digest.md)). **Done-when met:** a stale
source appears in `source_health`, in `atlas health` / `atlas digest`, and via `get_source_health`
— verified live (CLI + real stdio MCP) and by integration tests (stale/fresh/failing
classification; secret-free + audited tool; digest surfaces the stale source). **219 tests pass**;
ruff/mypy clean; migration `0005` applies, reverses, drift-check empty.

---

### Prior phases

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

---

## In progress

*(none)* — B8 closed. B9 not yet claimed.

---

## Next

**B9 — FRED connector + `time_series_point` + `get_macro_series`.**

B8 closed the freshness/health subsystem. B9 adds the first time-series source:

1. The **`time_series_point` table** (docs/schemas §2.7) — **RANGE-partitioned by `ts`** with a
   **composite PK `(id, ts)`** (the partitioned-table rule), `UNIQUE NULLS NOT DISTINCT (provider,
   series_id, ts, vintage)` (PG15+), `kind` discriminator (`observation`|`bar`), nullable OHLCV.
   Migration `0006` + ORM + schema. **First partitioned table — mind the composite-PK invariant.**
2. The **FRED connector** (`atlascore.connectors.fred`) — API connector via the SDK lifecycle;
   `credential_ref` names the `FRED_API_KEY` env key (never the value); honor documented rate
   limits; **the `?api_key=…` URL must be sanitized before any `crawl_job`/`audit` write** (the
   `sanitize_for_storage` path already strips URL query/userinfo — verify it covers FRED).
   Fixtures: success/empty/malformed/rate-limit/drift.
3. **`get_macro_series`** MCP tool (docs/mcp §2.2) — a series + ordered observations; `value` as a
   string to preserve `numeric` precision; capped (`≤2000`); audited; never zero-fills gaps.

**Definition of done for B9:** query a macro series via MCP.

Build-order reminders: B10 SEC EDGAR + NVD; B11 enrichment + `get_entity_mentions` (also unblocks
the deferred `search_documents` topic/entity filters); B12 summaries + `get_document_summary`; B13
the three rule-based alerts + delivery (wires the real digest channel + freshness alerts) +
`get_alerts`; B14 backups + purge; B15 hardening. The scheduler block (wires the timed
`job_type=freshness` job + single-scheduler liveness) is still pending.

---

## Known issues

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
  filtering is a single `source` slug (not `source_ids[]`), and the `topics`/`entity_ids` filters are
  **deferred to B11** (the columns are returned per hit but stay empty until enrichment runs). The
  `truncated` flag is a `len(hits) >= limit` heuristic (no separate count query), so it can read
  `true` when exactly `limit` rows exist with none behind them. Documented in
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
- **Digest delivery is stdout-only (B8)** — `atlas digest` prints/logs the digest; the real
  `ALERT_WEBHOOK_URL`/`NTFY_TOPIC`/SMTP channel and the freshness **alert** are wired in B13. The
  digest **text** (`build_digest`) is final, so B13 only swaps the transport.
- **Scheduler-liveness half of the digest is a placeholder (B8)** — only fetch-worker presence is
  checked (via `pg_locks` on the fetch advisory lock); the single-scheduler liveness check lands
  with the scheduler block.
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
