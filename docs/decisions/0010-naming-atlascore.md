# 0010. Name the project AtlasCore (distribution `atlascore`, CLI `atlas`) and retire AtlasWire

- **Status:** Accepted
- **Date:** 2026-06-03
- **Deciders:** Operator (single-operator project)
- **Spec reference:** AtlasCore Canonical Decisions Spec v1 (date 2026-06-03), section 0 (identity), section 1 (repo tree, packaging note), section 11 (`console_scripts: atlas`), section 12 (docs map, `PROJECT-PLAN.md`); correction 12 (plan-file reconciliation).

> ADRs are immutable once Accepted. To change this decision, write a new ADR and
> mark this one *Superseded by*. Where this file and the canonical spec disagree,
> the spec wins and this ADR must be corrected.

## Context

The project was originally named **AtlasWire** (the plan file on disk is
`AtlasWire-Personal-Plan.md` and still uses `AtlasWire`/`atlaswire` throughout).
A single, consistent identity is required so that the distribution name, import
package, CLI command, brand-in-prose, repo root, and every document agree
exactly — the canonical spec is declared the single source of truth and
downstream docs must match it character-for-character.

Constraints shaping the choice:

- **One repository / one Python distribution.** A monorepo with `apps/` and
  `packages/` mapping into a single distribution.
- **A short, memorable CLI verb** the operator types daily
  (`atlas source validate`, `atlas crawl once`, `atlas backup`, `atlas purge`).
- **FOSS-publishable posture.** The name will appear on PyPI / a public repo if
  published later (code-not-data), so it must be coherent and unambiguous.
- **No lingering dual identity.** Two names in flight cause exactly the kind of
  doc/spec drift this project is trying to avoid.

## Decision

The project adopts a single canonical identity:

| Facet | Value |
| --- | --- |
| Project / repository / Python distribution name | **`atlascore`** |
| Product / brand name in prose and docs | **AtlasCore** |
| CLI command (`console_scripts` entry point) | **`atlas`** |
| Repo root directory | `atlascore/` |
| Tagline | *A governed, single-operator data fabric for your own agents and dashboards.* |

The CLI is a Typer app; the **exact module path behind the `atlas` console
script is finalized in MVP block B1** (the packaging/import layout is decided
there, not now). The name itself is fixed by this ADR.

**AtlasWire is fully retired.** Every `AtlasWire` / `atlaswire` reference is
renamed to `AtlasCore` / `atlascore` across the codebase and docs. The **only
permitted remaining AtlasWire reference** is a single documented
"Formerly AtlasWire" redirect note in the old plan file after it is migrated.

**Plan-file reconciliation (correction 12).** As part of adopting this name:

1. Rename `AtlasWire-Personal-Plan.md` → `docs/PROJECT-PLAN.md`.
2. Delete the two conflicting ordered backlogs in that file — the
   "Build first (in order)" list (around line 474) and the "25.2 First 10 tasks"
   list (around line 600) — and replace both with a pointer to the single
   canonical MVP backlog (spec section 8, blocks **B1–B15**).
3. Globally rename `AtlasWire` → `AtlasCore` and `atlaswire` → `atlascore` in
   that file, keeping exactly one documented "Formerly AtlasWire" note.

This is what makes section 0's "the only remaining AtlasWire reference is one
redirect note" and section 8's "reconcile all three lists" claims true on disk.

## Consequences

- **Positive:**
  - One unambiguous identity across distribution, import, CLI, brand, repo, and
    docs — no dual-name drift.
  - Short daily CLI verb (`atlas`) decoupled from the distribution name
    (`atlascore`), which is a common, clean Python pattern.
  - The repo is publish-ready under one coherent name when the OSS switch
    (code-not-data) happens.

- **Negative / costs:**
  - A one-time rename sweep across files, plus the plan-file migration and the
    removal of two stale backlogs.
  - Any external references to "AtlasWire" become stale (acceptable: the repo is
    private first).

- **Follow-ups / obligations:**
  - Perform the `AtlasWire→AtlasCore` / `atlaswire→atlascore` rename and the
    `docs/PROJECT-PLAN.md` migration (correction 12), leaving one "Formerly
    AtlasWire" note.
  - Set `name = "atlascore"` and the `atlas` `console_scripts` entry in
    `pyproject.toml`; finalize the module path in B1.
  - Ensure README, CLAUDE.md, CHANGELOG.md, and every `docs/**` file use
    AtlasCore / `atlascore` / `atlas` consistently.

- **Revisit triggers:**
  - A naming/trademark conflict discovered at publish time (would require a new
    ADR to rename again).
  - A decision to split the monorepo into multiple distributions (would change
    the distribution-name mapping; new ADR).
