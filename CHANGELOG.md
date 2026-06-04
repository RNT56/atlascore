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
SSRF-guarded fetcher + worker**, and the **B5 article parser + canonical `document` + parse
worker**. The MVP continues at block **B6**.

### Added

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

[Unreleased]: https://example.invalid/atlascore/compare/HEAD
