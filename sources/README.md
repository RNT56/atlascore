# Source registry

This directory is the **git-tracked source of truth** for AtlasCore's sources. Each
`<id>.yaml` is one source, in the canonical shape documented in
[`../docs/connectors/README.md`](../docs/connectors/README.md) §4. The file is mirrored
into the Postgres `source` table by `atlas source sync`.

**Rules (CLAUDE.md §3, §10, §16):**

- **No secrets, no private endpoints.** `credentials.ref` names an *environment-variable
  key* resolved at runtime (e.g. `FRED_API_KEY`), **never** a secret value. Source YAML is
  publishable as code-not-data.
- **Default-deny.** A source is fetched only when `policy.status` is `approved` (or
  `restricted` with encoded constraints) **and** `enabled: true`. Anything else is disabled;
  validation refuses to *enable* a non-approved source.
- **Filename == id.** The file stem must equal the source `id` slug (lowercase
  `provider_dataset`).

**Workflow:**

```bash
atlas source validate --all      # schema + duration grammar + default-deny (no DB; CI gate)
atlas source sync --all          # validate + register/upsert into the source table
atlas source test <id>           # dry-run the default-deny policy gate for a registered source
atlas source list                # list registered sources
```

Adding a source: see the add-a-source workflow in `docs/connectors/README.md` §12. Set
`policy.status: pending` until you have vetted the source's terms.
