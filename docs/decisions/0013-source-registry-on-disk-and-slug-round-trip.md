# 0013. Source registry lives in `sources/`; the YAML slug round-trips in `config`; B2 adds `source sync`/`list`

- **Status:** Accepted
- **Date:** 2026-06-03
- **Deciders:** Operator (single-operator project)
- **Spec reference:** AtlasCore Canonical Decisions Spec v1 (2026-06-03), §10 source rules, docs/connectors §4 (canonical YAML + §4.6 round-trip), docs/schemas §2.1 (`source`); MVP block **B2**.

## Context

B2 implements the source registry: the git-tracked canonical YAML, default-deny
validation, and the `atlas source` CLI. Three details are under-specified and must be
fixed to build it:

1. **Where the registry lives.** The spec calls the YAML the "git-tracked source of
   truth, mirrored into the `source` table" but does not name a directory. CLAUDE.md §7
   says not to add top-level directories without a clear reason.
2. **The YAML `id` vs the table `id`.** docs/connectors §4.2 defines YAML `id` as a
   lowercase `provider_dataset` **slug** ("logical key, used in dedupe_keys and the
   filename"), and §4.6 maps `id` to a "dedicated column". But docs/schemas §2.1 makes
   the table `id` a **UUIDv7 primary key**. A slug is not a UUID, so `id` cannot be both.
3. **CLI surface.** CLAUDE.md §9 lists `atlas source validate` and `atlas source test`,
   but the scheduler (a later block) reads sources from the **table**, so the registry
   must be persisted; there is no command for that yet. §9 says: "If a command doesn't
   exist yet, add it."

## Decision

1. **Registry directory: `sources/`** (one new top-level dir; the clear reason is "the
   git-tracked source registry"). Each source is `sources/<slug>.yaml`; the filename stem
   must equal the YAML `id`. It contains no secrets and no private endpoints (publishable
   code-not-data). `sources/README.md` documents the rules. CLAUDE.md §7 is updated to
   include it.

2. **The table `id` stays a UUIDv7 PK; the YAML slug round-trips in `source.config.slug`.**
   `SourceYAML.to_record()` puts the slug in the `config` jsonb (the R9 round-trip home,
   alongside `allowed_uses`/`robots`/`connector`/`schedule`/`timeout_seconds`). This
   reconciles §4.6 ("id → dedicated column") with §2.1 ("id = uuid PK"): the *logical*
   key is the slug (in `config`), the *physical* key is the UUIDv7. **Upserts are keyed on
   the stable slug** (`config->>'slug'`, falling back to `name` for legacy rows without one):
   re-sync — including after a `name` change — updates the same row (its UUIDv7 is kept), so a
   rename never orphans a row. The unique constraint on `name` still blocks two sources sharing a
   display name. docs/connectors §4.6 is annotated with this mapping.

3. **B2 CLI = `validate` (pure, no DB; the CI `source-yaml` target), `sync` (validate +
   upsert into `source`, idempotent), `test` (policy dry-run), `list`.** `sync` and `list`
   are added under §9's "add it" allowance; CLAUDE.md §9 is updated to list them.
   **`atlas source test` is a no-network policy dry-run in B2** — it runs the default-deny
   gate and prints the fetch plan; **live reachability lands with the connector in B4**
   (the ingestion/control plane must not depend on a network call here, and the SSRF guard
   that any real fetch requires does not exist until B4). `base_url` is validated for
   scheme/format only in B2.

## Consequences

- **Positive:** the registry is a discoverable, reviewable directory; the slug↔UUID split
  is explicit and keeps sortable UUIDv7 PKs while preserving the human/logical slug;
  re-syncing is idempotent; `validate` needs no DB so the CI gate and pre-fetch checks stay
  cheap and offline.
- **Negative / costs:** the slug is not a first-class column, so slug-based queries/upserts go
  through `config->>'slug'` with no unique index in B2 — safe for single-operator manual sync, but
  not race-proof against concurrent syncs. Changing a source's *slug* while keeping its *name*
  collides on the unique `name` (intentional — the slug is the identity). `source test` cannot
  prove reachability in B2 (deferred to B4).
- **Follow-ups / obligations:** CLAUDE.md §7 (+`sources/`) and §9 (+`sync`/`list`) updated
  with this change; docs/connectors §4.6 annotated with the slug-in-config mapping; if
  slug-keyed integrity becomes important, add a unique index on `(config->>'slug')` or a
  dedicated `slug` column via a migration + ADR.
- **Revisit triggers:** sources need to be renamed routinely (then key upsert on slug with
  a real unique constraint), or the registry grows subdirectories/multiple operators.
