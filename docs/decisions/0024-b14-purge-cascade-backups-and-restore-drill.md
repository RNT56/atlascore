# 0024. B14 erasure + backups: `atlas purge` full cascade, shared-raw guard, `atlas backup` + restore drill

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Operator (single-operator project)
- **Spec reference:** AtlasCore Canonical Decisions Spec v1 (2026-06-03), CLAUDE.md §16 (R3 purge cascade) / §12 (storage, backups) / §19 (purge regression), docs/runbooks §4–§6; MVP block **B14** (builds on every prior table).

## Context

B14 adds the two durability/erasure obligations: tested backups + restore, and the audited
right-to-erasure path. The forced decisions:

1. **Shared raw blobs vs "delete the raw on purge".** One `raw_document` (a whole RSS feed / SEC
   submissions blob) backs **many** `document` rows (one fetch → N per-GUID documents → one raw).
   The runbook's step 6 ("delete the raw file + row") cannot run while siblings still reference the
   raw — the FK is `RESTRICT`.
2. **The multi-doc summary leak (R3).** A multi-scope `summary` has `document_id = NULL` and links
   members only via `member_doc_ids[]`; a `document_id`-only delete would leave purged personal data
   in `summary_text`/`citations`.
3. **Idempotency + erasure proof.** Re-running a purge must be a clean no-op, and erasure must be
   provable without retaining the personal data.
4. **A real restore drill that doesn't depend on flaky tooling** in this environment (the harness
   sandbox blocks subprocess venv creation; host `pg_dump` may be absent).

## Decision

- **`storage.purge` runs the full R3 cascade in one transaction** (`atlas purge <id> --yes`):
  embedding → mention → summary (`document_id` **OR** `member_doc_ids[]` — the GIN arm closes the
  multi-doc leak) → document (drops it from FTS) → raw → alert-evidence reconciliation → a
  **retained** `audit_log` entry (`retention_class='purge'`, `actor='cli'`) recording **content
  hashes + counts only, never the personal data**. An entity purge deletes the entity + its mentions
  and reconciles the non-referential `document.entity_ids[]`; documents survive. `purge_target`
  dispatches a document first, else an entity, else a clean no-op.
- **Shared-raw guard.** After deleting the target document, the raw file/row is deleted **only when
  no other document references it** (a `COUNT` check). Until the last sibling is purged, the shared
  raw is retained — RESTRICT forbids otherwise. `RawStore.delete` is the **single** place a raw blob
  is removed (idempotent, `missing_ok`). **Honest limitation:** a shared raw still holds the purged
  item's bytes among its siblings until its last document is purged; full raw erasure happens then.
  This reconciles runbook §6 step 6 (which is now "delete the raw, *if unreferenced*").
- **Idempotent + audited.** A missing target returns `found=False` and writes **no** new audit row
  (the original erasure entry already proves it — "never double-count", runbook §6). Alert evidence
  reconciliation removes the id from `evidence_doc_ids[]`; an alert losing **all** evidence becomes
  `status='purged'`.
- **Backups: `infra/backup.sh` (operational) + `atlas backup` (on-demand) + `infra/restore_drill.sh`
  (the tested restore).** `backup.sh` (from B1) does `pg_dump -Fc` + offsite rclone/rsync +
  retention; `atlas backup` is a cross-platform Python wrapper for a local snapshot (pg_dump +
  a copy of the content-addressed raw store) whose **password goes via `PGPASSWORD`, never argv**
  (§14) — `build_pg_dump_plan` is unit-tested for that. `restore_drill.sh` restores a dump into a
  **throwaway scratch DB**, runs sanity counts (incl. that `retention_class='purge'` audit rows
  survive a restore), and drops the scratch DB — never touching the live database.

## Consequences

- **Positive.** The done-when (restore verified; purge works and is audited) is met; the
  multi-doc-summary regression is covered (the load-bearing R3 case); erasure is provable and
  idempotent; the shared-raw reality is handled correctly and honestly rather than pretending a
  single purge erases a shared blob.
- **Negative / accepted (see STATUS Known issues).** A shared raw is not erased until its last
  document is purged (documented). `atlas backup` requires `pg_dump` on PATH (errors cleanly if
  absent; `infra/backup.sh` is the canonical operational path). The restore drill is a runbook
  script + a live drill, not a CI job (it needs `pg_dump`/`pg_restore` binaries + a scratch DB). No
  audit-log **rotation** job exists yet (purge entries are flagged `retention_class='purge'` for it;
  the rotation that deletes only `routine` rows lands with the scheduler block).
