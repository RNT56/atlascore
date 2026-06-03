# NNNN. Short, present-tense title of the decision

- **Status:** Proposed | Accepted | Superseded by [NNNN](NNNN-filename.md) | Deprecated
- **Date:** YYYY-MM-DD
- **Deciders:** Operator (single-operator project)
- **Spec reference:** AtlasCore Canonical Decisions Spec v1 (date 2026-06-03), section(s) / resolution(s) this ADR encodes

> Use this template for every AtlasCore Architecture Decision Record. Copy it to
> `NNNN-kebab-case-title.md`, fill in every section, and add a row to
> `docs/decisions/README.md`. ADRs are immutable once Accepted: to change a
> decision, write a new ADR and mark this one *Superseded by*. Keep ADRs short,
> traceable, and consistent with the canonical spec — where this file and the
> spec disagree, the spec wins and the ADR must be corrected.

## Context

What is the situation that forces a decision? Capture the forces at play:
constraints (lean, single-box, internal-only, provenance-first, runs-unattended),
the problem being solved, options considered, and any relevant facts about the
data model, repo tree, or operating posture. State the problem neutrally; do not
pre-justify the decision here.

## Decision

The decision, stated in active voice as something the project *does*. Be
concrete: name exact tables, columns, env vars, CLI commands, MCP tools, repo
paths, and MVP blocks (B1–B15) where relevant, so downstream docs and code can
match it exactly. If the decision encodes a numbered spec resolution (e.g. R1),
say so explicitly.

## Consequences

What becomes easier and what becomes harder as a result. Include:

- **Positive:** what this unlocks or simplifies.
- **Negative / costs:** the accepted tradeoffs.
- **Follow-ups / obligations:** migrations, runbooks, tests, docs, or future
  ADRs this decision creates a duty to write.
- **Revisit triggers:** the concrete conditions under which we would reopen this
  decision (e.g. "more than one fetch worker", "publishing results externally").
