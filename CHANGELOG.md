# Changelog

All notable changes to AtlasCore are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

This is a private, internal-only project (FOSS boundary is code, not data). Until the
first tagged release, all work accumulates under **[Unreleased]**.

## [Unreleased] - 2026-06-03

This entry covers the planning + documentation scaffold, the **B1 skeleton** (the `atlascore`
package, the Alembic harness, and the `source` schema), and the **B2 source registry** (canonical
YAML, default-deny, and the `atlas source` CLI). The MVP continues at backlog block **B3**.

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

[Unreleased]: https://example.invalid/atlascore/compare/HEAD
