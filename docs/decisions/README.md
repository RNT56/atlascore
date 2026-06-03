# Architecture Decision Records (ADRs)

This directory holds AtlasCore's Architecture Decision Records. Each ADR captures
one significant decision using the **Context / Decision / Consequences** structure
(see [`0000-template.md`](0000-template.md)). ADRs are written for genuinely
architecture-changing decisions; routine choices live in the relevant `docs/`
deep-dive instead.

**Conventions**

- One decision per file, named `NNNN-kebab-case-title.md`.
- Status is one of: `Proposed`, `Accepted`, `Superseded by NNNN`, `Deprecated`.
- ADRs are immutable once `Accepted`. To change a decision, write a new ADR and
  mark the old one `Superseded by`.
- Every ADR is consistent with the **AtlasCore Canonical Decisions Spec v1**
  (2026-06-03); where an ADR and the spec disagree, the spec wins and the ADR is
  corrected.
- Add a row to the index below whenever you add an ADR.

## Index

| ADR | Title | Status | Date | Spec resolution |
|-----|-------|--------|------|-----------------|
| [0001](0001-single-postgres-one-box.md) | Single PostgreSQL instance on one box | Accepted | 2026-06-03 | — |
| [0002](0002-document-current-state-reproducible-not-retained.md) | `document` holds current state; old parses reproducible, not retained | Accepted | 2026-06-03 | R2 |
| [0003](0003-unified-time-series-and-null-vintage-uniqueness.md) | Unified `time_series_point` with NULLS NOT DISTINCT vintage uniqueness | Accepted | 2026-06-03 | R1 |
| [0004](0004-structured-retention-shape.md) | Structured per-data-class retention; drop scalar `retention_ref` | Accepted | 2026-06-03 | R6 |
| [0005](0005-freshness-target-end-to-end.md) | Freshness target, end to end | Accepted | 2026-06-03 | R7 |
| [0006](0006-summary-table-and-content-hash-cache.md) | Dedicated `summary` table with content-hash cache | Accepted | 2026-06-03 | R4 |
| [0007](0007-embedding-backend-abstraction-and-fixed-dim.md) | Abstract the embedding backend behind one interface with a single fixed dimension per deployment | Accepted | 2026-06-03 | R8 |
| [0008](0008-rate-limiting-across-workers.md) | Enforce host-scoped rate limiting with a single fetch worker now and a Postgres token bucket later | Accepted | 2026-06-03 | R5 |
| [0009](0009-purge-erasure-vs-immutable-raw.md) | Carve out one audited purge path as the single exception to raw immutability, with a full referential cascade | Accepted | 2026-06-03 | R3 |
| [0010](0010-naming-atlascore.md) | Name the project AtlasCore (distribution `atlascore`, CLI `atlas`) and retire AtlasWire | Accepted | 2026-06-03 | — |
| [0011](0011-mcp-read-only-seven-tool-surface.md) | Expose a read-only seven-tool MCP surface over stdio with injection safety and side-effect confirmation instead of RBAC | Accepted | 2026-06-03 | — |
| [0012](0012-packaging-layout-src-atlascore.md) | Adopt a single `src/atlascore` import root; preserve `packages`/`apps` as the subpackage grouping | Accepted | 2026-06-03 | — (B1 packaging) |
| [0013](0013-source-registry-on-disk-and-slug-round-trip.md) | Source registry in `sources/`; YAML slug round-trips in `config`; B2 adds `source sync`/`list` | Accepted | 2026-06-03 | — (B2 registry) |

> ADRs 0001–0011 encode the canonical-spec resolutions R1–R8 plus the naming and
> MCP-surface decisions: R1/R2 in 0003/0002, R3 in 0009, R4 in 0006, R5 in 0008,
> R6 in 0004, R7 in 0005, and R8 in 0007. **0012** records the B1 packaging-layout
> finalization (the `src/atlascore` import root); **0013** records the B2 source-registry
> location + slug round-trip + CLI surface.
