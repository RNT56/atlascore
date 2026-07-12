# AtlasCore — Compliance Posture

> **Date:** 2026-06-03
> **Scope:** This document is the compliance reference for AtlasCore. It implements SPEC section 10 (Compliance Posture) and the related schema/design resolutions (R3 purge, R6 retention shape, R7 freshness). It is consistent with `docs/schemas/README.md`, `docs/security/README.md`, and `docs/runbooks/README.md`. Where this document and the SPEC ever diverge, the SPEC wins.
>
> **One-line posture:** AtlasCore is a single-operator, internal-only data fabric. It collects from RSS feeds and official/free APIs, keeps full attribution, never redistributes data, sends no data to social platforms, and supports a working, audited right-to-erasure path. It does **not** claim to strip all personal data — that would be a lie about news/article text.

---

## 1. Foundational Constraints (the things that make compliance simple)

AtlasCore's compliance story is short because four decisions were made up front and are not negotiable:

1. **Internal-only.** Data is collected for one operator's own agents and dashboards. It is **never redistributed**, resold, syndicated, or published. There is no multi-tenant surface, no public API of harvested data, no export-for-others feature.
2. **FOSS boundary is code, not data.** Repository code is Apache-2.0. Harvested data, operator configuration, raw stores, backups, and credentials are never published.
3. **RSS + official/free APIs only.** No browser fleet. No social-platform ingestion. No paywall bypass. This keeps Terms-of-Service and aggregation responsibility with the source/vendor, not with AtlasCore.
4. **Runs unattended on one box.** Single operator, single VM. No third-party data processors are involved *unless* the operator opts into cloud embeddings/LLM (see §5), which is treated as a deliberate, documented exception with its own caveats.

Everything below is the consequence of these four decisions.

---

## 2. Internal-Only + Attribution

- **No redistribution.** AtlasCore stores and indexes data for the operator's personal research and operational use only. There is no data-licensing-for-resale concern because nothing leaves the box as data.
- **Personal-research posture.** Output (search results, summaries, alerts, MCP responses) is for the operator. It is not a product or a feed for others.
- **Always keep attribution.** Every externally sourced record carries attribution: **source, canonical URL, author (where available), and timestamp(s)**. This is enforced at the schema level — see the `document`, `raw_document`, and `source` tables in `docs/schemas/README.md`. Attribution is non-optional: a record without provenance is a bug, not a feature.
- **Provenance on every surface.** MCP tools (`search_documents`, `get_document_summary`, `get_macro_series`, `get_entity_mentions`, `get_source_health`, `get_alerts`, `get_raw_document`) return provenance with every result. Summaries and alerts cite internal evidence document IDs. Claims trace back to raw evidence.

> **If the posture ever changes** — i.e. you start publishing or sharing results derived from this data — the internal-only assumption no longer holds and several things below change (see §3, §6). Revisit this document before doing that.

---

## 3. PII Posture — the **honest** version

AtlasCore deliberately does **not** claim "we drop all PII." That claim would be false: news articles, filings, RSS items, and issue threads inherently contain personal data — names, quotes, author bylines, executives, public officials. You cannot ingest article full text and honestly assert it is PII-free.

Instead, the real posture has four explicit parts:

### 3.1 GDPR personal / household exemption (while purely personal)

As long as processing is **purely personal or household activity** — for the operator's own research, not published or shared, not used for any professional or commercial purpose, and not used to profile or target other people — it falls under the GDPR Article 2(2)(c) "purely personal or household activity" exemption (the EU operator's relevant frame).

**This exemption is conditional and it lapses** the moment the activity stops being purely personal — for example, if you **publish, share, or commercialise** results derived from the data, or use it to make decisions about other individuals. See §3.4.

### 3.2 Data minimization where feasible

We minimize personal-data exposure where it is practical without breaking the core function:

- **No social-platform ingestion.** This removes the single largest source of dense personal data and profiling material.
- **No targeting or profiling of individuals.** AtlasCore is not built to track, score, or build dossiers on named natural persons. `entity` rows exist for companies, assets, topics, and public events — not for profiling private individuals.
- **Drop obviously-unneeded personal fields at parse.** Where a parser can drop a clearly-unneeded personal field (e.g. commenter emails, user handles, raw contact details in a feed payload) without losing the substantive content, it does. This is best-effort minimization, **not** a guarantee of PII removal.
- **Cap result and token volume.** MCP and RAG surfaces cap result sizes, limiting bulk extraction of personal data.

We do **not** overclaim. Article body text will contain names and quotes; that is accepted and disclosed here rather than hidden behind a false "PII-stripped" label.

### 3.3 A working, audited erasure path

AtlasCore implements a real right-to-erasure / deletion path: **`atlas purge`** (full cascade per resolution **R3**), described in §4. It is idempotent, audited, and erases raw payloads too. This is the control that makes the honest posture defensible: even though personal data is present, it can be located and provably erased.

### 3.4 The exemption lapses on publish / share — revisit then

If the operator ever **publishes, shares, syndicates, or commercialises** results, the household exemption no longer applies and AtlasCore becomes a data controller with full GDPR obligations (lawful basis, transparency, data-subject rights at scale, possibly DPIA, records of processing, etc.). **None of that machinery is built.** Before crossing that line:

- Re-read this document and SPEC §10.
- Treat it as a new project decision requiring an ADR (`docs/decisions/`).
- Do not assume the current posture covers a published/shared workload — it does not.

> **Rule of thumb:** *Never overclaim a control you cannot implement.* We implement minimization-where-feasible and audited erasure. We do **not** implement, and do not claim, full PII removal or a published-controller compliance program.

---

## 4. Right to Erasure — `atlas purge` (full cascade, R3)

Erasure is the load-bearing control for the honest PII posture. It must work, be idempotent, and be provable.

**Command:**

```bash
atlas purge <doc_id|entity_id>
```

### 4.1 Cascade given a `document_id` (R3)

When purging a document, the operation deletes or reconciles **every** place the document and its personal data can live:

1. Delete `embedding` rows where `document_id = <doc_id>`.
2. Delete `mention` rows where `document_id = <doc_id>`.
3. Delete `summary` rows referencing the document, in **both** linkage shapes:
   - `summary.document_id = <doc_id>` (single-scope summaries), **and**
   - `<doc_id> = ANY(summary.member_doc_ids)` (multi-scope summaries, which set `document_id = null` and link only via the `member_doc_ids[]` array — backed by a `GIN(member_doc_ids)` index).
   Affected multi-doc summaries are **recomputed or hard-deleted** so that purged personal data does not survive in `summary_text` or `citations`. (This closes the gap where a `document_id`-only delete would have missed multi-doc summaries.)
4. Remove the document from the FTS index.
5. Delete the `document` row.
6. Delete the `raw_document` **file on disk** and the `raw_document` row. Raw immutability has exactly **one** documented exception: this audited purge path.
7. Scan `alert.evidence_doc_ids[]` and remove `<doc_id>`. If an alert loses **all** its evidence, set that alert's `status = purged`.
8. Write a **RETAINED** `audit_log` entry recording the action and content hashes — **never the personal data itself**. This entry has `retention_class = 'purge'` so the rotation job never removes it; erasure stays provable (see §5.3).

### 4.2 Cascade given an `entity_id` (R3)

When purging an entity:

1. Delete the `entity` row.
2. Delete that entity's `mention` rows.
3. **Documents survive** — purging an entity does not delete the documents that mentioned it.
4. Write a retained purge `audit_log` entry as above.

### 4.3 Idempotency, FK behavior, and array reconciliation

- **Idempotent.** Running `atlas purge` twice on the same id is safe and a no-op the second time.
- **FK ON DELETE = NO ACTION / RESTRICT (R3, correction 17).** Foreign keys use `NO ACTION`/`RESTRICT` so the application-driven purge ordering above is **DB-enforced** and an accidental `ON DELETE CASCADE` cannot over-delete (e.g. wiping documents on an entity purge). The purge job deletes in dependency order; the DB refuses out-of-order deletes.
- **Array-of-id columns are intentionally non-referential.** `document.entity_ids[]`, `document.topics[]`, `alert.evidence_doc_ids[]`, and `summary.member_doc_ids[]` are **not** FKs. They are reconciled only by the purge job (and other maintenance jobs), never by DB cascade.
- **Regression test required.** A purge regression test covers the multi-doc-summary case specifically (a `summary` with `document_id = null` linked only via `member_doc_ids[]`), proving that purged content does not survive in any summary.

**Runbook:** the operational, step-by-step erasure procedure and verification checklist live in `docs/runbooks/README.md` (purge runbook). This document defines *what* must happen and *why*; the runbook defines *how* to run and verify it.

---

## 5. Retention Classes

Retention is **source/data-class specific**. "Public" does **not** mean "indefinitely retainable." The canonical retention shape is **structured `jsonb`** on the `source` table and in the source YAML (resolution **R6**); the old scalar `retention_ref` string is removed everywhere.

### 5.1 Canonical retention shape (source YAML → `source.retention` jsonb)

```yaml
retention:
  raw: "2y"            # duration grammar: Nd | Nw | Nm | Ny, or "permanent"
  normalized: "permanent"
  audit: "180d"        # optional
```

Duration grammar: `Nd` (days), `Nw` (weeks), `Nm` (months), `Ny` (years), or the literal `"permanent"`.

### 5.2 Retention by data class

| Data class | Default retention | Notes |
|---|---|---|
| **Raw payloads** (`raw_document` + disk files) | **1–2 years**, then prune low-value | Immutable, gzipped, content-addressed on disk. Deletable **only** via the purge path (records an audit entry). Pruning low-value raw after 1–2y is a lifecycle policy, not silent deletion. |
| **Normalized records** (`document`, `time_series_point`, `entity`, etc.) | **kept** | Small and useful; `document` holds current state (last-write-wins on re-parse, reproducible from immutable raw — R2). Deletable via purge. |
| **Summaries** (`summary`) | with parent docs / prune with raw | Content-hash cached; purged with their member documents (§4.1 step 3). |
| **Embeddings** (`embedding`) | with parent doc | Purged with the document. |
| **Alerts** (`alert`) | **6–12 months** | `purged` status when all evidence is erased. |
| **Audit — routine** (`audit_log`, `retention_class='routine'`) | **rotate after a few months** | Operational noise; rotated by the rotation job. |
| **Audit — purge/erasure** (`audit_log`, `retention_class='purge'`) | **RETAINED (never rotated away)** | Keeps erasure provable. This is a distinct retention class. |
| **`source_health`** | current snapshot only | No history table in MVP. |
| **`crawl_job`** | 90 days (done/dead pruned) | DLQ (`status=dead`) inspectable via CLI before pruning. |

### 5.3 Audit retention is two classes, by column (correction 14)

The `audit_log` table has an explicit **`retention_class`** column (values `routine` | `purge`), set to `purge` for erasure entries and `routine` otherwise. The rotation job deletes **only** `retention_class = 'routine'` rows beyond the retention window. This avoids fragile action-string matching and guarantees that erasure records survive rotation — erasure-provability depends on it.

### 5.4 No silent deletes

There are no silent deletes anywhere. Removal happens through:

- The **lifecycle/prune** path (bounded, observable, e.g. low-value raw after 1–2y), or
- The **purge/erasure** path (`atlas purge`), which always writes a retained audit entry.

---

## 6. Cloud Backends and PII Egress (correction 5)

SPEC §6 makes cloud embedding and LLM backends a **first-class supported option** (`EMBEDDING_BACKEND=local|openai|google`, `LLM_BACKEND=local|api|none`) because the operator may lack a powerful local machine. That convenience has a direct compliance consequence that must be stated plainly rather than glossed over:

> **When `EMBEDDING_BACKEND` or `LLM_BACKEND` is set to a cloud provider, document text — which §3 admits contains personal data — egresses to a third-party (and likely third-country) processor.** Sending full article/filing text to OpenAI/Google/Anthropic-style hosted models is **not** consistent with a pure internal-only / household-exemption posture without qualification.

Two acceptable ways to handle this; pick deliberately and record it:

1. **Local-required posture for PII-bearing docs (preferred for the strict reading).** Require `EMBEDDING_BACKEND=local` and `LLM_BACKEND=local|none` for any corpus where personal-data egress is unacceptable. Local sentence-transformers / local LLM keep everything on the box. This preserves the household-exemption framing cleanly.

2. **Documented-processor posture (if cloud is used).** If the operator chooses cloud for capacity reasons, then:
   - **Acknowledge** that a third-party data processor is involved and, likely, a cross-border transfer.
   - **Configure** the vendor for **no-training / zero-retention / data-processing-addendum (DPA)** settings wherever the provider offers them.
   - **Set a hard monthly spend cap** (`LLM_MONTHLY_SPEND_CAP_EUR`) when `LLM_BACKEND=api`.
   - **Stop describing** cloud LLM/embedding use as "pure internal-only / household-exemption" — it is a documented processor relationship. Record the choice (and the no-training/zero-retention/DPA settings) in an ADR.

Either way: **do not** describe first-class cloud backends as automatically consistent with the internal-only / no-egress posture. They are a deliberate trade-off, not a default-free option.

Provenance is unaffected by backend choice: every `embedding` row records `model_name` + `model_version` + `dim`; every `summary` row records `model_name` + `model_version` + `prompt_version`. Cached summaries are never recomputed for an identical `(content, model, prompt)` tuple, which also bounds repeat egress.

---

## 7. Robots, Rate Limits, and Allowed Uses

### 7.1 Robots is a **policy input**, not a general runtime gate

`robots` (in the source YAML `policy.robots`, mirrored into the `source` config/policy column) is a **policy input consulted by the human operator when setting source policy** — and consulted at runtime **only** for the rare permitted HTML/browser source. It is **not** a general runtime gate for API/RSS sources, where robots.txt does not govern documented API/feed access in the first place.

### 7.2 No HTML/browser source ships in MVP → no runtime robots gate is built (correction 18)

To be explicit and honest: **no HTML/browser source ships in the MVP (B1–B15).** Therefore:

- **No runtime robots-checking code is built in B1–B15.**
- The `robots` field is **advisory / policy-input only** in the MVP.
- A runtime robots check is added **inside the connector's own block** at the time an HTML/browser source is first approved — not before, and not speculatively.

This pairs with the source-table mapping: the `robots` and `allowed_uses` values round-trip from YAML into the `source` table (see `docs/schemas/README.md` and SPEC correction 9) but are consumed as policy metadata, not as a runtime fetch gate for API/RSS.

### 7.3 Rate limits and allowed uses

- **Documented API rate limits are respected** (e.g. SEC EDGAR's 10 req/s; FRED's published limits). Rate limiting is host-scoped and correct under concurrency: MVP uses **exactly one fetch worker** with in-process per-host token buckets, with a startup guard preventing a second fetch worker until the documented `host_rate_state` scale-up path is enabled (SPEC §5 / R5 / correction 16). `Retry-After` is always honored.
- **Official/free APIs are preferred over scraping.** API/RSS first; static HTML only if permitted; browser as a last, approved exception.
- **`allowed_uses`** (e.g. `[internal_search, internal_summary, agent_retrieval]`) is recorded per source and reflects how that source's data may be used internally.

### 7.4 Default-deny policy

`policy.status` defaults to **`unknown` → disabled**. Only `approved` (or `restricted` with encoded constraints) is ever fetched. `unknown`, `pending`, and `prohibited` sources are never fetched (local fixtures excepted). This is enforced in the source registry and the connector lifecycle, not just documented here.

---

## 8. Secrets and SOPS Boundary

The secrets posture is: **no plaintext secrets in the repo, ever.**

### 8.1 Baseline (always)

- Real secrets live in **`.env` (gitignored)** and are resolved into **environment variables** at runtime.
- `.env.example` (committed) documents the variable names with no values.
- A source's `credential_ref` / `credentials.ref` names an **env key** resolved at runtime; it is **never** a secret value. Source YAML contains **no secrets and no private endpoints**.
- Secrets are **never printed or logged**. Free-form sinks (`crawl_job.last_error`, `crawl_job.payload`, `audit_log.detail`) are passed through one `sanitize_for_storage` helper (`packages/security/secrets`) before persistence: known-secret patterns redacted, URL query/userinfo stripped, `Authorization`/`Cookie` headers removed. **Backups inherit this** because the sanitized values are what get written to Postgres in the first place (SPEC correction 8). The B15 secrets-in-logs regression test covers `last_error` and `audit_log.detail`.

### 8.2 Optional SOPS/age-encrypted file — concrete shape (correction 15)

An **age/SOPS-encrypted file MAY be committed**, with a concrete, implementable shape:

- The tree may contain `secrets.sops.yaml` (encrypted) and a `.sops.yaml` recipient config.
- The decryption key lives **outside the repo** — in a password manager or on a hardware key — and is pointed to by an env hint such as `SOPS_AGE_KEY_FILE`.
- Deploy step (runbook): `sops -d secrets.sops.yaml > .env` at deploy time, then run from the gitignored `.env`.

This keeps "no plaintext secrets in repo" true: what is committed is ciphertext; the key is never in the repo. See the deploy/secrets runbook in `docs/runbooks/README.md`.

---

## 9. FOSS Boundary and Licensing

- **Publish code, never data.** Connectors, schemas, parsers, and the SDK may be open-sourced. **Harvested data, derived indexes, embeddings, summaries, and credentials are never published.**
- **No hardcoded private credentials or licensed feeds** in the repository — ever.
- **Public code, private data.** Repository code is licensed under **Apache-2.0** (ADR 0040). Harvested content and deployment state remain internal-only.
- **Publication gates.** Ruff, mypy, Python 3.12/3.13 tests with coverage, security regressions, dependency audit, package build, secrets scanning, and mandatory PG16 integration protect the code-not-data boundary.
- **Publishing changes the GDPR picture** (§3.4). Publishing *code* is fine; publishing *results/data derived from the corpus* lapses the household exemption. Keep the two strictly separate.

---

## 10. What NOT to Touch

AtlasCore must **never** ingest from, or attempt to access, the following in this build:

- **Licensed market/news wires** (e.g. paid newswire feeds, licensed market-data terminals/redistribution feeds).
- **Paywalls** — no paywall bypass, no credential abuse, no CAPTCHA circumvention.
- **Broker / trading APIs.**
- **Social platforms** — no social-platform ingestion. If social *signal* is ever wanted, use an aggregator (e.g. a LunarCrush-style connector) so ToS and aggregation responsibility sit with the vendor, not with AtlasCore.

Also prohibited (from SPEC §3, restated for compliance completeness): illegal scraping, proxy/IP rotation to evade rate limits or geoblocking or bans, fake accounts, ban evasion, deceptive identity masking, and fetching any source whose `policy.status` is `unknown`, `pending`, or `prohibited` (local fixtures excepted).

---

## 11. Compliance Checklist (per change)

Before merging a change that touches ingestion, schema, MCP, or storage, confirm:

- [ ] New/changed sources have `policy.status` set; default-deny respected (`unknown`/`pending`/`prohibited` disabled).
- [ ] Attribution (source, URL, author, timestamps) is preserved end-to-end.
- [ ] Retention is expressed as structured `jsonb` (raw / normalized / optional audit); no scalar `retention_ref`.
- [ ] `atlas purge` still cascades correctly, including `summary.member_doc_ids[]` (multi-doc summaries) and `alert.evidence_doc_ids[]`; purge regression test passes.
- [ ] Purge writes a **retained** (`retention_class='purge'`) audit entry with hashes, not personal data.
- [ ] No secrets, private endpoints, or personal data added to repo, logs, `crawl_job`/`audit_log` jsonb, MCP output, tests, or docs.
- [ ] If a cloud embedding/LLM backend is in use, the processor reality is documented (no-training/zero-retention/DPA), spend cap set, and the household-exemption framing is qualified (§6).
- [ ] No licensed wire / paywall / broker / social source touched.
- [ ] If results are being published/shared, the household exemption is reassessed (§3.4) and an ADR written.

---

## 12. Related Documents

- `CLAUDE.md` — agent operating contract; mission, prohibitions, compliance summary.
- `docs/PROJECT-PLAN.md` — rationale, roadmap, MVP backlog (B1–B15).
- `docs/schemas/README.md` — the 12 canonical tables, retention/freshness jsonb shapes, `retention_class`, FK ON DELETE behavior.
- `docs/security/README.md` — SSRF/URL-safety, sanitization, secrets handling, prompt-injection separation.
- `docs/mcp/README.md` — the 7-tool MCP surface, provenance, `get_raw_document` injection handling.
- `docs/runbooks/README.md` — operational procedures: **purge/erasure runbook**, backup/restore drill, SOPS decrypt-at-deploy step, embedding backfill+reindex.
- `docs/decisions/` — ADRs, including any decision to go cloud-backend, to publish, or to switch to an OSS license.
