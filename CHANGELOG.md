# Changelog

All notable changes to AtlasCore are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

This is a private, internal-only project (FOSS boundary is code, not data). Until the
first tagged release, all work accumulates under **[Unreleased]**.

## [Unreleased] - 2026-06-03

This entry establishes the planning, documentation, and project scaffolding. No
application code ships yet; the MVP begins at backlog block **B1**.

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

[Unreleased]: https://example.invalid/atlascore/compare/HEAD
