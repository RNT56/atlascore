# 0004. Structured per-data-class retention; drop scalar `retention_ref` (R6)

- **Status:** Accepted
- **Date:** 2026-06-03
- **Deciders:** Operator (single-operator project)
- **Spec reference:** AtlasCore Canonical Decisions Spec v1 (2026-06-03), sections 2.1, 4, 10, resolution R6

## Context

AtlasCore keeps three operationally distinct data classes per source, each with a
different retention rule:

- **raw** — gzipped, content-addressed payloads on disk; large; pruned after
  1–2 years for low-value sources ("public" does not mean "indefinitely
  retainable").
- **normalized** — `document`, `time_series_point`, entity/mention rows; small and
  useful; typically kept ("permanent").
- **audit** — append-only `audit_log`; routine entries rotate after a few months,
  while purge/erasure entries are retained indefinitely (see ADR-level note in
  the compliance posture and resolution R3/correction 14).

An earlier design carried a single scalar `retention_ref` string per source. One
scalar cannot express three independent durations, forces an out-of-band lookup to
mean anything, and silently encourages applying one retention to all classes —
which is wrong (raw and normalized must differ). It also does not round-trip
cleanly between the git-tracked source YAML and the `source` table.

## Decision

**This is resolution R6.** Retention is expressed as a **structured, per-data-class
object**, and the scalar `retention_ref` string is **DROPPED everywhere** (source
YAML, `source` table, code, docs).

**Canonical YAML shape** (the git-tracked source of truth, section 4):

```yaml
retention:
  raw: "2y"            # duration grammar below, or "permanent"
  normalized: "permanent"
  audit: "180d"        # optional
```

**Duration grammar:** `Nd` | `Nw` | `Nm` | `Ny` (days / weeks / months / years),
or the literal `"permanent"`. `audit` is optional; when omitted, the global audit
rotation policy applies (routine entries rotate; purge/erasure records are always
retained — that retention class is enforced regardless of this field).

**Storage:** the object is stored verbatim in **`source.retention jsonb`** (spec
2.1), mirroring the YAML so it **round-trips losslessly** between git and the
table. No scalar retention column exists.

**Example in a full source record** (section 4):

```yaml
id: sec_edgar_companyfacts
name: SEC EDGAR Company Facts
# ...
retention: { raw: "2y", normalized: "permanent" }
```

**Semantics:**

- `raw` governs pruning of `raw_document` files + rows for that source (low-value
  raw pruned after the configured window; deletion only via the audited purge
  path, never a silent unlink — see R3).
- `normalized` governs normalized records derived from the source; `"permanent"`
  means kept indefinitely at single-operator scale.
- `audit` (optional) is advisory for that source's routine audit entries; the
  purge-record retention class always overrides toward retention.

Retention is **source/data-class specific by design**: there is no global blanket
duration. Public availability never implies indefinite retainability.

## Consequences

**Positive**

- One source declaration expresses all three retention classes explicitly; no
  hidden indirection, no accidental "same retention for raw and normalized".
- Lossless round-trip between the YAML source-of-truth and `source.retention
  jsonb`; the registry table is self-describing.
- Aligns with the compliance posture (section 10): raw 1–2y then prune,
  normalized kept, audit routine rotates with purge-records retained.

**Negative / costs**

- A `jsonb` column is less constrained than typed columns; the duration grammar
  must be validated at the boundary (Pydantic schema + `atlas source validate`)
  rather than by the database.
- Three retention dials per source is slightly more to author than one scalar —
  accepted, because conflating them was the actual bug.

**Follow-ups / obligations**

- The Pydantic source schema validates the retention object and duration grammar;
  `atlas source validate` (B2) rejects malformed retention.
- The retention/prune job and `atlas purge` (B14) read `source.retention` and
  apply per-class windows; raw pruning always goes through the audited purge path
  (R3, ADR-tracked separately).
- Remove every lingering `retention_ref` reference from docs, schemas, and the
  plan file as part of the same change (spec section 12 / correction 12).

**Revisit triggers**

- A genuine need for finer-grained retention (e.g. per-topic or legal-hold
  windows) would extend the object's grammar in a new ADR — not a return to a
  scalar.
