# AtlasCore — STATUS (living board)

> **This is the shared coordination board for every session (human or AI) and every worker.**
> Read [`../CLAUDE.md`](../CLAUDE.md) and this file before starting. Update it at session close.
> Last updated: **2026-06-03**.

---

## Current phase

**Block B1 — NOT STARTED.**

AtlasCore is pre-MVP / greenfield. Planning and the full documentation scaffold exist; no
application code has been written. The next concrete work is initializing the repository and
standing up the B1 skeleton.

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

---

## In progress

*(none)* — no work-area is currently claimed.

---

## Next

**B1 — Repo + monorepo skeleton + Docker Compose (Postgres 16 + pgvector) + CI + pre-commit +
Alembic harness + `source` schema; finalize the Python import/packaging layout.**

Concrete next steps:

1. Initialize the git repository (private) and commit the scaffold.
2. Finalize the Python import/distribution layout (src vs flat; how `apps/` and `packages/`
   map into the `atlascore` distribution; the exact module path behind the `atlas` console script).
3. `infra/docker-compose.yml`: PostgreSQL 16 with `pgvector`; early migration runs
   `CREATE EXTENSION IF NOT EXISTS vector` and `CREATE EXTENSION IF NOT EXISTS pg_trgm`.
4. Wire CI and pre-commit so they run green; stand up the Alembic harness.
5. Implement the `source` table (with `config`/`policy` jsonb that round-trips the full canonical
   source YAML: `policy.allowed_uses`, `policy.robots`, `fetch.connector`, `fetch.schedule`,
   `fetch.timeout_seconds`).

**Definition of done for B1:** `make test` green; migrations run.

Then B2 (source registry + default-deny + `atlas source validate/test`), and onward through B15.
Note the build-order dependency: **B7** ships `search_documents` + the `audit_log` table +
result caps; **B8** adds `source_health` + the freshness job + `get_source_health` + the daily
digest (`atlas health` / `atlas digest`).

---

## Known issues

- Repository is **not yet git-initialized**; the scaffold is on disk but uncommitted.
- **No application code yet** — only docs, ADRs, config, and the folder skeleton exist.
- The Python import/packaging layout is intentionally **not finalized** until B1; scaffolding
  files declare intent and tree, not the final import graph.

---

## Open questions

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
