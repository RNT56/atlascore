# 0012. Adopt a single `src/atlascore` import root; preserve `packages`/`apps` as the subpackage grouping

- **Status:** Accepted
- **Date:** 2026-06-03
- **Deciders:** Operator (single-operator project)
- **Spec reference:** AtlasCore Canonical Decisions Spec v1 (2026-06-03), §1 packaging note (the import/distribution layout is *finalized in B1*), §7 repository layout; MVP block **B1**.

## Context

CLAUDE.md §7 lays out the canonical tree with two top-level source roots —
`packages/` (libraries: `core`, `schemas`, `connectors_sdk`, `connectors`,
`parsers`, `enrichment`, `storage`, `security`, `alerts`) and `apps/` (runnable
roles: `cli`, `mcp_server`, `worker`, `scheduler`, `api`) — and explicitly defers
*"src vs flat; how `apps/` and `packages/` map into the `atlascore` distribution;
the exact module path behind the `atlas` console script"* to block **B1**.

Two hard constraints force the decision:

1. **The import convention is fixed by the spec.** Connector references are
   dotted paths like `atlascore.connectors.sec.companyfacts:SecCompanyFactsConnector`,
   the console script is `atlascore.cli.main:app`, and the secrets helper lives at
   `atlascore.security.secrets`. Every subpackage (`connectors`, `cli`, `security`,
   …) must sit **directly under `atlascore`**, not under an intermediate
   `packages`/`apps` path component.

2. **On-disk path must equal import path, or the toolchain breaks.** If code
   physically lived at `packages/<x>/…` while importing as `atlascore.<x>.…`, the
   on-disk path never contains `atlascore`. `mypy` (strict, a CI gate) names and
   resolves modules from their filesystem path; it cannot rename `packages/core` to
   `atlascore.core`, and editable-install path remaps are invisible to static
   analysis. The result is unresolved-import and duplicate-module errors. `ruff`
   isort, `pytest` collection, and coverage have the same path-vs-import friction.
   Hatchling can remap directories into the wheel, but that only fixes the built
   artifact, not the dev/lint/type-check loop.

Options considered: (a) **flat** — `packages/<x>` importable as `<x>` (rejected:
violates the mandated `atlascore.*` convention); (b) **remap** — keep
`packages/`/`apps/` and map them into `atlascore` via Hatchling `sources`
(rejected: irreducible `mypy`/`pytest` path-vs-import friction; "clever, not
boring" per §18); (c) **src layout** — one `src/atlascore/` import root.

## Decision

AtlasCore uses a **`src/atlascore/` layout**. All Python code lives under
`src/atlascore/`, so on-disk path equals import path and `ruff`/`mypy`/`pytest`/
coverage/runtime all agree with no remapping.

The spec's `packages/` vs `apps/` distinction is **preserved as the subpackage
names directly under `atlascore`**, and as a documented convention:

- **libraries** (formerly `packages/`): `atlascore.core`, `atlascore.schemas`,
  `atlascore.connectors_sdk`, `atlascore.connectors`, `atlascore.parsers`,
  `atlascore.enrichment`, `atlascore.storage`, `atlascore.security`, `atlascore.alerts`.
- **runnable roles** (formerly `apps/`): `atlascore.cli`, `atlascore.mcp_server`,
  `atlascore.worker`, `atlascore.scheduler`, `atlascore.api`.

**Mapping rule (canonical):** wherever the spec/docs say `packages/<x>/…` read
`src/atlascore/<x>/…`, and wherever they say `apps/<y>/…` read
`src/atlascore/<y>/…`. The dotted import is `atlascore.<x>` / `atlascore.<y>` in
both cases — exactly the spec's connector/console-script convention.

Concrete settings (pyproject.toml):

- Build: `[tool.hatch.build.targets.wheel] packages = ["src/atlascore"]`; a
  `src/atlascore/py.typed` marker ships type information (PEP 561).
- Console script: `atlas = "atlascore.cli.main:app"` (unchanged from the scaffold).
- `ruff`: `src = ["src", "tests"]`, `known-first-party = ["atlascore"]`.
- `mypy`: `mypy_path = "src"`, `packages = ["atlascore"]` (so `uv run mypy` checks
  the package by import name, no duplicate-module ambiguity); CI runs `uv run mypy`.
- coverage: `source = ["src/atlascore"]`.

The two top-level placeholder dirs `apps/` and `packages/` (which held only
`.gitkeep`) are **removed**; this is a net *reduction* in top-level directories.

## Consequences

- **Positive:** zero path-vs-import friction across the whole toolchain; the
  modern, standard Python packaging shape; editable install is the simple
  lenient mode (`src` on `sys.path`), verified to import every B1 subpackage; the
  spec's import convention (`atlascore.connectors.sec…`, `atlascore.cli.main:app`)
  is honored verbatim.
- **Negative / costs:** the literal directory prefixes in the spec and the deep-dive
  docs (`packages/security/secrets`, `apps/cli`, …) no longer exist on disk; they
  now mean `src/atlascore/security/secrets`, `src/atlascore/cli`, etc. via the
  mapping rule above. CLAUDE.md §7's tree is updated to the finalized layout in the
  same change; the deep-dive `docs/**/README.md` path prefixes are reconciled
  per-block as each subsystem's code lands (same-change doc updates).
- **Follow-ups / obligations:** CLAUDE.md §7 updated (done with this ADR);
  `docs/STATUS.md` Open-questions item "import/packaging layout not finalized"
  resolved; subsequent blocks adding subpackages place them under `src/atlascore/`.
- **Revisit triggers:** the project genuinely splits into multiple independently
  versioned/distributed packages (a real monorepo with several wheels), at which
  point a workspace of `packages/*` projects each with its own `pyproject.toml`
  would be reconsidered — not needed for a single-operator, single-distribution build.
