# 0009. Carve out one audited purge path as the single exception to raw immutability, with a full referential cascade

- **Status:** Accepted
- **Date:** 2026-06-03
- **Deciders:** Operator (single-operator project)
- **Spec reference:** AtlasCore Canonical Decisions Spec v1 (date 2026-06-03), resolution R3 (with R2, R4, and corrections 4, 14, 17); sections 2.3 (`raw_document`), 2.4 (`document`), 2.5 (`summary`), 2.6 (`embedding`), 2.9 (`mention`), 2.10 (`alert`), 2.11 (`audit_log`), 10 (compliance); MVP block B14.

> ADRs are immutable once Accepted. To change this decision, write a new ADR and
> mark this one *Superseded by*. Where this file and the canonical spec disagree,
> the spec wins and this ADR must be corrected.

## Context

Two invariants are in tension:

1. **Raw immutability + provenance.** `raw_document` is the immutable evidence
   anchor. Every `document`, `embedding`, `summary`, and `mention` traces back to
   a raw payload; re-parsing is reproducible because it re-runs a pinned
   `parser_version` against the unchanged raw (R2). Mutating raw would break
   provenance and auditability — operating priority #2.

2. **Working erasure (GDPR / data minimization).** The compliance posture
   (section 10) is honest that full article text inherently contains personal
   data, and relies on **a working, audited erasure path** as one of its pillars.
   Erasure that cannot reach the raw payload on disk is not erasure at all.

These cannot both be absolute. We need exactly one, narrow, audited way to
violate immutability, and that path must remove personal data *everywhere it
landed* — including the array-of-id columns (`entity_ids`, `topics`,
`evidence_doc_ids`, `member_doc_ids`) that no foreign key protects.

## Decision

This ADR encodes **resolution R3**.

**Immutability has exactly one documented exception: the audited `atlas purge`
path.** Nothing else mutates or deletes `raw_document`. `atlas purge` accepts a
`doc_id` or an `entity_id`, is **idempotent**, and always writes a **retained**
`audit_log` entry recording the action and content hashes — **never the personal
data itself**.

### Cascade given a `doc_id`

In one transaction (multi-table canonical write):

1. Delete `embedding` rows where `document_id = doc_id`.
2. Delete `mention` rows where `document_id = doc_id`.
3. Delete `summary` rows where **`document_id = doc_id` OR
   `doc_id = ANY(member_doc_ids)`** (correction 4). Single-scope summaries match
   by `document_id`; multi-scope summaries set `document_id = NULL` and link only
   via `member_doc_ids[]`, so a `document_id`-only delete would leave purged PII
   alive in `summary_text`/`citations`. Affected multi-doc summaries are hard-
   deleted (or recomputed from the surviving members); they are reproducible from
   cache rules (R4) and need not be retained. A `GIN(member_doc_ids)` index makes
   this scan efficient.
4. Remove the document from the FTS index.
5. Delete the `document` row.
6. Delete the `raw_document` **file on disk** and its row — this is the single
   immutability carve-out.
7. Scan `alert.evidence_doc_ids[]` and remove `doc_id`; any alert that loses
   **all** evidence transitions to `status = purged`.
8. Write a **retained** `audit_log` entry: `action`, `actor`, `target_ref`, and
   the relevant content hashes — no personal data.

### Cascade given an `entity_id`

1. Delete the `entity` row and its `mention` rows.
2. **Documents survive** (an entity purge is about the entity, not the sources
   that mentioned it).
3. Write a retained `audit_log` entry as above.

### Referential / FK behaviour (correction 17)

- Foreign keys use **`ON DELETE NO ACTION` / `RESTRICT`** (not `CASCADE`). The
  application drives deletion order explicitly (children before parents), and the
  database *enforces* that ordering, preventing an accidental
  `ON DELETE CASCADE` from over-deleting (e.g. an entity purge silently wiping
  documents).
- Array-of-id columns (`document.entity_ids`, `document.topics`,
  `alert.evidence_doc_ids`, `summary.member_doc_ids`) are **intentionally
  non-referential**. They carry no FK and are reconciled **only by the purge
  job** via the explicit scans above.

### Audit retention split (correction 14)

`audit_log` gains a **`retention_class`** column (or boolean `retained`):

- Purge/erasure entries are written with `retention_class = 'purge'` and are
  **retained indefinitely** so erasure stays provable.
- All other entries are `retention_class = 'routine'`.
- The rotation job deletes **only** `retention_class = 'routine'` entries beyond
  the routine window (a few months). It distinguishes classes by this column, not
  by fragile action-string matching.

### Secret-safe storage of audit detail

`audit_log.detail` (like `crawl_job.last_error` and `crawl_job.payload`) is a
free-form sink. Per the storage-sanitization invariant, all such values pass
through the single `sanitize_for_storage` helper in `packages/security/secrets`
before persistence, so purge audit entries never capture credentials or
credential-bearing URLs. (Covered in its own resolution; noted here because the
purge entry is retained forever and must stay clean.)

## Consequences

- **Positive:**
  - Erasure is real and complete: raw bytes on disk, derived rows, FTS entries,
    and array-linked references (including multi-doc summaries) all go.
  - Provenance/immutability stays honest everywhere *except* the one named,
    audited path.
  - `RESTRICT` FKs make purge ordering DB-enforced; an accidental cascade cannot
    over-delete.
  - The retained purge audit class keeps erasure provable while letting routine
    audit noise rotate away.

- **Negative / costs:**
  - Purge logic must be kept in lockstep with the schema: every new table or
    array-of-id column that can hold document-derived PII must be added to the
    cascade.
  - Hard-deleting multi-doc summaries on purge means they must be recomputed if
    still wanted (acceptable; they are cache, R4).
  - `RESTRICT` FKs require the app to delete children first; a wrong order errors
    loudly (which is the intent).

- **Follow-ups / obligations:**
  - Implement `atlas purge <doc_id | entity_id>` with the exact cascade (B14).
  - Add the `GIN(member_doc_ids)` index and the `audit_log.retention_class`
    column via Alembic migration.
  - Set `ON DELETE NO ACTION/RESTRICT` on all relevant FKs in the schema.
  - Write the erasure runbook in `docs/runbooks/README.md` and the erasure
    posture in `docs/compliance/README.md`.
  - **Purge regression tests (correction 4):** include a case that purges a
    document which is a member of a **multi-doc summary** and asserts that the
    summary (and its `summary_text`/`citations`) is gone; assert idempotency
    (a second purge is a no-op) and that the purge `audit_log` entry is retained
    and free of personal data.

- **Revisit triggers:**
  - A new derived store or array column that can hold document PII (extend the
    cascade + tests).
  - Publishing results externally (the household exemption lapses; erasure
    obligations and scope may expand — section 10).
  - Any proposal to add a second mutation path to raw (would require superseding
    this ADR).
