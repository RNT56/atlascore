# AtlasCore — STATUS (living board)

> **This is the shared coordination board for every session (human or AI) and every worker.**
> Read [`../CLAUDE.md`](../CLAUDE.md) and this file before starting. Update it at session close.
> Last updated: **2026-06-03**.

---

## Current phase

**Block B3 — DONE (2026-06-03). Next: B4.**

The raw store and the `crawl_job` queue are in place and verified end-to-end against a real
Postgres 16: migration `0002` (`crawl_job` status-machine + DLQ, `raw_document` content-addressed),
the immutable gzip raw store, the queue (`SELECT … FOR UPDATE SKIP LOCKED`, retry→pending with
exponential backoff, →`dead` over `max_attempts`, stale-claim reclaim), `sanitize_for_storage`
(redacts secrets before `crawl_job.last_error`/`payload`), and the `atlas crawl enqueue`/`dlq` CLI.
**Done-when met:** integration tests demonstrate claim/complete, fail→retry→dead, SKIP-LOCKED across
two workers, and stale reclaim; raw payloads are gzipped + content-addressed on disk; `atlas crawl
enqueue` enqueued a fetch job. `make test` green offline (92 unit pass; 15 integration skip);
15 integration tests pass with a DB. **B1** and **B2** are also DONE.

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

---

## In progress

*(none)* — B3 closed. B4 not yet claimed.

---

## Next

**B4 — RSS connector (ETag/Last-Modified) + fetcher (SSRF/URL-safety + per-host rate limiting).**

Concrete next steps:

1. `connectors_sdk`: the `Connector` ABC + result types + a contract-test harness (no live network).
2. `security/url_safety`: the SSRF guard (scheme allowlist; reject loopback/RFC1918/CGNAT/link-local/
   ULA/IPv4-mapped/metadata; resolve + pin the validated IP; re-validate every redirect hop) — applied
   to the fetcher AND any agent-supplied URL.
3. The fetcher: shared SSRF-guarded `httpx` client, conditional requests (ETag/If-None-Match,
   Last-Modified/If-Modified-Since), `DEFAULT_USER_AGENT`, per-host in-process token bucket, and the
   **single-fetch-worker startup guard** (R16/R5) — refuses a second fetch worker until the
   `host_rate_state` scale-up path exists.
4. The generic RSS connector → store raw (the B3 raw store) before parse; dedupe on feed GUID;
   the worker loop that *runs* a fetch `crawl_job` and `atlas crawl once`.
5. Fixtures: success/empty/malformed/rate-limit/drift/deleted (no live network, respx/vcrpy).

**Definition of done for B4:** RSS items fetched, raw captured, duplicate GUIDs ignored.

Then onward through B15. Build-order dependencies to remember: **B7** ships `search_documents` +
the `audit_log` table + result caps; **B8** adds `source_health` + the freshness job +
`get_source_health` + the daily digest (`atlas health` / `atlas digest`). `get_source_health`
must NOT be implemented before B8.

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
- **Single-fetch-worker startup guard (R16/R5) is NOT yet built** — it lands in **B4** with the
  fetcher (no fetch worker runs until then). `atlas crawl once` (enqueue + RUN) also lands in B4.

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
