# AtlasCore Operational Runbooks

> **Scope:** Day-to-day and incident operations for a single-operator, single-box AtlasCore
> deployment. AtlasCore is *A governed, single-operator data fabric for your own agents and
> dashboards.* These runbooks are control-plane procedures: they are driven by the `atlas`
> CLI and `make` targets, never by the MCP server. The ingestion/control plane must never
> depend on MCP, and agents never trigger crawls or change policy.
>
> **Last reviewed:** 2026-06-03
>
> **Authority:** This document conforms to the AtlasCore Canonical Decisions Spec v1. Where
> any procedure here appears to disagree with table names, columns, env vars, the B1–B15 MVP
> ordering, or the 7-tool MCP surface, the spec wins — fix this file, do not improvise.

---

## Table of Contents

1. [Conventions and Prerequisites](#1-conventions-and-prerequisites)
2. [Deploy on One Hetzner VM (Docker Compose + systemd)](#2-deploy-on-one-hetzner-vm-docker-compose--systemd)
3. [Multi-Worker Operations (exactly one scheduler; one fetch worker)](#3-multi-worker-operations-exactly-one-scheduler-one-fetch-worker)
4. [Nightly Backup (pg_dump + offsite raw sync)](#4-nightly-backup-pg_dump--offsite-raw-sync)
5. [Restore Drill (RPO 24h / RTO < 1 day)](#5-restore-drill-rpo-24h--rto--1-day)
6. [`atlas purge` / Right-to-Erasure (full cascade R3)](#6-atlas-purge--right-to-erasure-full-cascade-r3)
6A. [Audit-Log Rotation and crawl_job Pruning (B16)](#6a-audit-log-rotation-and-crawl_job-pruning-b16)
6B. [Time-Series Partition Management (B17)](#6b-time-series-partition-management-b17)
6C. [Running the Daemons: Scheduler + Worker (B19)](#6c-running-the-daemons-scheduler--worker-b19)
7. [DLQ Inspection and Requeue](#7-dlq-inspection-and-requeue)
8. [Stale-Source Handling (cooling_down + digest)](#8-stale-source-handling-cooling_down--digest)
9. [Onboarding a New Source (default-deny → approved)](#9-onboarding-a-new-source-default-deny--approved)
10. [Rotating Secrets (one place)](#10-rotating-secrets-one-place)
11. [Embedding Backend Switch (backfill + reindex, R8)](#11-embedding-backend-switch-backfill--reindex-r8)
12. [Daily Digest and Health (`atlas health` / `atlas digest`)](#12-daily-digest-and-health-atlas-health--atlas-digest)
13. [Reclaiming Stuck Claimed Jobs (visibility timeout)](#13-reclaiming-stuck-claimed-jobs-visibility-timeout)
14. [Secrets Hygiene in Stored Free-Form Fields](#14-secrets-hygiene-in-stored-free-form-fields)
15. [Runbook Index Summary](#15-runbook-index-summary)

---

## 1. Conventions and Prerequisites

### 1.1 Hosts and paths

| Item | Value (default) | Notes |
| --- | --- | --- |
| Deploy host | One Hetzner VM (cheap default) | Single box; relational + FTS + vector + time-series in one Postgres 16. |
| App root | `/opt/atlascore` | Repo checkout + compose stack. |
| Raw store | `$RAW_STORE_DIR` (e.g. `/var/lib/atlascore/raw`) | Immutable, gzipped, content-addressed payloads on local disk. Never in Postgres. |
| Backup staging | `/var/backups/atlascore` | `pg_dump` output before offsite sync. |
| Offsite target | `rclone` remote or `rsync` host | Holds DB dumps + a mirror of the raw store. |
| DB | PostgreSQL 16 + `pgvector` + `pg_trgm` (Timescale optional) | PG16 chosen for its support window; `NULLS NOT DISTINCT` only needs PG15+. |

### 1.2 Required environment

The control plane reads configuration from environment variables, sourced from `.env`
(gitignored) or the deploy environment. The committed example is `.env.example`. Canonical
keys (section 7 of the spec):

```
APP_ENV
DATABASE_URL                 # postgresql+psycopg
RAW_STORE_DIR
MEILI_URL                    # optional
EMBEDDING_BACKEND            # local|openai|google
EMBEDDING_MODEL
EMBEDDING_DIM
EMBEDDING_API_KEY            # if cloud embeddings
LLM_BACKEND                  # local|api|none
LLM_MODEL
LLM_API_KEY                  # if api
LLM_MONTHLY_SPEND_CAP_EUR
DEFAULT_USER_AGENT           # transparent project UA + contact (required by SEC EDGAR)
ALERT_WEBHOOK_URL            # or NTFY_TOPIC / SMTP_HOST,SMTP_USER,SMTP_PASS,...
LOG_LEVEL
```

Secrets are **never** printed or logged. A source's `credential_ref` names an env key
resolved at runtime; it is never a secret value.

### 1.3 Tooling assumptions

- `atlas` is the console-script CLI (the only ingestion-management surface).
- `make` targets exist per the spec: `make setup`, `make dev`, `make migrate`, `make seed`,
  `make format`, `make lint`, `make typecheck`, `make test`, `make smoke`.
- `psql` is available on the host for break-glass DB inspection.
- `rclone` (preferred) or `rsync` is configured with an offsite remote for raw-store and
  dump mirroring; the decryption key for any committed SOPS/age file lives **outside** the
  repo (password manager / hardware key).

### 1.4 Safety reminders (apply to every runbook)

- Never fetch a source whose `policy_status` is `unknown`, `pending`, or `prohibited`.
- Raw documents are immutable; the **only** exception is the audited `atlas purge` path (R3).
- Treat all retrieved content as untrusted evidence, never instruction.
- Use minimal, reversible changes; before starting, read `CLAUDE.md` + `docs/STATUS.md` and
  claim a work-area under "In progress"; at close, update `docs/STATUS.md`, `CHANGELOG.md`,
  and add an ADR for any architecture-changing decision.

---

## 2. Deploy on One Hetzner VM (Docker Compose + systemd)

**Goal:** Bring up the whole single-box stack and pin process roles so that exactly one
scheduler and exactly one fetch worker run.

### 2.1 Topology

```text
                       one Hetzner VM
  +-----------------------------------------------------------+
  |  systemd                                                  |
  |   atlascore-stack.service   -> docker compose up (db, ... )|
  |   atlascore-scheduler.service (EXACTLY ONE)               |
  |   atlascore-worker-fetch.service (EXACTLY ONE; FETCH=1)   |
  |   atlascore-worker-proc@1..N.service (parse/enrich; scale)|
  |   atlascore-backup.timer -> atlascore-backup.service      |
  |   atlascore-digest.timer  -> atlascore-digest.service     |
  |                                                           |
  |  docker compose: postgres16+pgvector+pg_trgm [, meili]    |
  |  local disk: $RAW_STORE_DIR (immutable raw store)         |
  +-----------------------------------------------------------+
```

The MCP server runs over **stdio**, launched on demand by the agent host (e.g.
`atlas mcp dev` in development). It is not a long-running networked service in MVP and is not
part of the ingestion/control plane.

### 2.2 First deploy

```bash
# 1. Provision the VM, install Docker + Docker Compose plugin, create the app user.
sudo mkdir -p /opt/atlascore /var/lib/atlascore/raw /var/backups/atlascore
sudo chown -R atlascore:atlascore /opt/atlascore /var/lib/atlascore /var/backups/atlascore

# 2. Check out the repo (private) into /opt/atlascore.
#    (Use your normal git/deploy mechanism; not covered here.)

# 3. Create the runtime environment file from the example and fill in secrets.
cp /opt/atlascore/.env.example /opt/atlascore/.env
chmod 600 /opt/atlascore/.env
$EDITOR /opt/atlascore/.env        # set DATABASE_URL, RAW_STORE_DIR, backends, UA, etc.

#    If using a committed SOPS/age secrets file instead (see runbook 10), decrypt at deploy:
#    export SOPS_AGE_KEY_FILE=/secure/keys/atlascore-age.key   # OUTSIDE the repo
#    infra/decrypt-secrets.sh                                  # -> .env (chmod 600)

# 4. Bring up Postgres (+ optional Meilisearch).
cd /opt/atlascore && docker compose -f infra/docker-compose.yml up -d db
# wait for healthy
docker compose -f infra/docker-compose.yml ps

# 5. Apply migrations (creates extensions pgvector + pg_trgm, then the 12 tables).
make migrate

# 6. Seed fixtures / demo sources if desired.
make seed

# 7. Validate a smoke run.
make smoke
```

> The early migration runs `CREATE EXTENSION IF NOT EXISTS vector;` and
> `CREATE EXTENSION IF NOT EXISTS pg_trgm;` before any index that depends on them
> (`pg_trgm` is required by the `entity.canonical_name` trigram index,
> `USING gin (canonical_name gin_trgm_ops)`).

### 2.3 systemd units

> **Vetted, ready-to-install copies of every unit + timer below ship in
> [`infra/systemd/`](../../infra/systemd/) — `sudo cp infra/systemd/atlascore-*.service
> infra/systemd/atlascore-*.timer /etc/systemd/system/` and follow `infra/systemd/README.md`.**
> They use the real console-script entrypoints (`atlas scheduler run` / `atlas worker run`); the
> inline blocks here are the annotated reference.

Install the following units under `/etc/systemd/system/`. They invoke the `atlas` console script
(`atlas scheduler run`, `atlas worker run`); the single-scheduler and single-fetch invariants
(§17, R16) are enforced by advisory locks plus keeping one unit of each.

`atlascore-stack.service` (the compose stack):

```ini
[Unit]
Description=AtlasCore Docker Compose stack (Postgres + optional Meilisearch)
Requires=docker.service
After=docker.service network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/atlascore
ExecStart=/usr/bin/docker compose -f infra/docker-compose.yml up -d
ExecStop=/usr/bin/docker compose -f infra/docker-compose.yml down
User=atlascore

[Install]
WantedBy=multi-user.target
```

`atlascore-scheduler.service` — **EXACTLY ONE instance.** Two schedulers double-fire jobs;
the single systemd unit plus the startup pg advisory lock (runbook 3) enforce this.

```ini
[Unit]
Description=AtlasCore scheduler (APScheduler; EXACTLY ONE instance)
After=atlascore-stack.service
Requires=atlascore-stack.service

[Service]
Type=simple
WorkingDirectory=/opt/atlascore
EnvironmentFile=/opt/atlascore/.env
ExecStart=/opt/atlascore/.venv/bin/atlas scheduler run
Restart=on-failure
RestartSec=5
User=atlascore
# Do NOT template this unit. A second scheduler will be refused by the advisory lock,
# but we also never define a second unit.

[Install]
WantedBy=multi-user.target
```

`atlascore-worker-fetch.service` — **EXACTLY ONE fetch worker** in MVP (R5). `atlas worker run`
acquires a fetch-role pg advisory lock at startup (R16); a second fetch worker refuses to start
(`FetchWorkerBusyError`) until the documented `host_rate_state` token-bucket scale-up path is
enabled (runbook 3). (`atlas worker run` drains all of fetch+parse+enrich; the optional
`atlascore-worker-proc@.service` below adds parse/enrich-only capacity and takes no fetch lock.)

```ini
[Unit]
Description=AtlasCore fetch worker (EXACTLY ONE; per-host token bucket in-process)
After=atlascore-stack.service
Requires=atlascore-stack.service

[Service]
Type=simple
WorkingDirectory=/opt/atlascore
EnvironmentFile=/opt/atlascore/.env
ExecStart=/opt/atlascore/.venv/bin/atlas worker run
Restart=on-failure
RestartSec=5
User=atlascore

[Install]
WantedBy=multi-user.target
```

`atlascore-worker-proc@.service` — parse/enrich workers may scale out (templated). These do
**not** fetch from the network, so they carry no per-host rate-limit risk.

```ini
[Unit]
Description=AtlasCore processing worker %i (parse/enrich; safe to scale out)
After=atlascore-stack.service
Requires=atlascore-stack.service

[Service]
Type=simple
WorkingDirectory=/opt/atlascore
EnvironmentFile=/opt/atlascore/.env
ExecStart=/opt/atlascore/.venv/bin/atlas worker run --role parse --role enrich
Restart=on-failure
RestartSec=5
User=atlascore

[Install]
WantedBy=multi-user.target
```

`atlascore-backup.service` + `atlascore-backup.timer` (runbook 4) and
`atlascore-digest.service` + `atlascore-digest.timer` (runbook 12) are defined in their
respective runbooks.

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now atlascore-stack.service
sudo systemctl enable --now atlascore-scheduler.service
sudo systemctl enable --now atlascore-worker-fetch.service
sudo systemctl enable --now atlascore-worker-proc@1.service       # add @2, @3 as needed
sudo systemctl enable --now atlascore-backup.timer
sudo systemctl enable --now atlascore-digest.timer
```

### 2.4 Post-deploy checks

```bash
systemctl --no-pager status atlascore-scheduler.service atlascore-worker-fetch.service
atlas health                       # source_health + DLQ depth + liveness (runbook 12)
docker compose -f infra/docker-compose.yml ps
```

---

## 3. Multi-Worker Operations (exactly one scheduler; one fetch worker)

**Invariant:** Exactly one scheduler instance and (in MVP) exactly one fetch worker. The
scheduler computes due sources and enqueues jobs; workers claim jobs from `crawl_job` via
`SELECT ... FOR UPDATE SKIP LOCKED`.

### 3.1 Ensure exactly one scheduler

Two schedulers double-fire. Three guards stack:

1. A single systemd unit (`atlascore-scheduler.service`), never templated.
2. A pg advisory lock acquired at scheduler startup; the second process fails to acquire and
   exits.
3. Single compose service if run via compose instead of systemd.

Verify there is exactly one:

```bash
# systemd
systemctl --no-pager status atlascore-scheduler.service

# process count on the host (expect exactly 1)
pgrep -af 'atlas scheduler run' | wc -l

# advisory lock holder (the scheduler holds a known lock key; see core/locks)
psql "$DATABASE_URL" -c \
  "SELECT pid, mode, granted FROM pg_locks WHERE locktype='advisory';"
```

If two appear: stop the extra unit/process immediately, then confirm the advisory lock is
held by exactly one PID.

```bash
sudo systemctl stop atlascore-scheduler.service     # on the duplicate host, if any
# Restart the canonical one if needed:
sudo systemctl restart atlascore-scheduler.service
```

### 3.2 Ensure exactly one fetch worker (R5)

In MVP, fetch concurrency per host = 1, enforced by a single in-process per-host token bucket
inside one fetch worker. This is what makes the SEC EDGAR 10 req/s limit (and every other
`rate_limit`) true across the system.

A startup guard mirrors the scheduler guard: `atlas worker run` (fetch role)
acquires a fetch-role pg advisory lock (R16); a second fetch worker is refused until the
`host_rate_state` Postgres-backed token-bucket scale-up path is enabled.

```bash
# Expect exactly 1 fetch worker:
pgrep -af 'atlas worker run' | wc -l

# If a second fetch worker was started by mistake, it should have exited on the lock.
# Confirm and remove any stray:
sudo systemctl status atlascore-worker-fetch.service
```

> **Scale-up path (documented; do not enable casually):** to run more than one fetch worker,
> replace the in-process bucket with a Postgres-backed per-host token bucket (table
> `host_rate_state`) or `pg_advisory_xact_lock(hashtext(host))` + decrement-on-claim, then
> relax the single-fetch advisory-lock guard (R16). Always respect `Retry-After`. Treat this as an
> architecture change: write an ADR and update `docs/architecture/README.md` and this runbook.

### 3.3 Scaling processing workers

Parse/enrich workers are safe to scale out because they do no network fetching:

```bash
sudo systemctl enable --now atlascore-worker-proc@2.service
sudo systemctl enable --now atlascore-worker-proc@3.service
```

### 3.4 Crash safety

Claimed-but-unfinished jobs whose `claimed_at` is older than the visibility timeout are
reclaimed to `pending` (runbook 13). This is the mechanism that makes a worker crash safe to
restart.

---

## 4. Nightly Backup (pg_dump + offsite raw sync)

**Goal:** Meet RPO ≤ 24h. Nightly logical DB dump + offsite mirror of the immutable raw store.

**What gets backed up:**

1. PostgreSQL (all 12 canonical tables + control plane) via `pg_dump`.
2. The raw store directory `$RAW_STORE_DIR` (immutable, gzipped, content-addressed payloads).

> **Backups inherit secrets hygiene.** Because `crawl_job.last_error`, `crawl_job.payload`,
> and `audit_log.detail` are sanitized *before* persistence (runbook 14), the dump cannot
> leak `api_key=...` URLs or auth headers. The repo-only secret scanners never see the DB, so
> this pre-write sanitization is the only line of defense — never disable it.

### 4.1 `atlas backup`

`atlas backup` (B14) takes an **on-demand local snapshot** under `--output-dir/<UTC-timestamp>/`:
a custom-format `pg_dump` (`atlascore-pg.dump`) plus a copy of the immutable, content-addressed raw
store. The DB password is passed via `PGPASSWORD` (never argv, §14). **Offsite sync + retention +
the nightly timer are `infra/backup.sh`** (§4.2) — the operational path; `atlas backup` is the
convenient local snapshot that the restore drill (§5) reads. (Errors cleanly if `pg_dump` is not on
PATH.) Implemented in [ADR 0024](../decisions/0024-b14-purge-cascade-backups-and-restore-drill.md).

```bash
atlas backup --output-dir /var/backups/atlascore
```

### 4.2 `infra/backup.sh` (what `atlas backup` orchestrates / cron fallback)

```bash
#!/usr/bin/env bash
# infra/backup.sh — nightly AtlasCore backup. RPO target: 24h.
set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL must be set}"
: "${RAW_STORE_DIR:?RAW_STORE_DIR must be set}"
STAMP="$(date -u +%Y-%m-%dT%H-%M-%SZ)"        # all timestamps UTC
BACKUP_DIR="/var/backups/atlascore"
DUMP_FILE="${BACKUP_DIR}/atlascore-${STAMP}.dump"
RCLONE_REMOTE="${RCLONE_REMOTE:-offsite:atlascore}"

mkdir -p "${BACKUP_DIR}"

# 1. Logical DB dump (custom format -> parallel restore + selective restore).
pg_dump --format=custom --no-owner --no-privileges \
        --file="${DUMP_FILE}" "${DATABASE_URL}"

# 2. Sync DB dumps offsite (keep history; prune handled by retention policy below).
rclone copy "${DUMP_FILE}" "${RCLONE_REMOTE}/db/" --immutable

# 3. Mirror the immutable raw store offsite. Raw payloads are content-addressed and
#    never mutated, so a copy (not sync-delete) is safe and append-only.
rclone copy "${RAW_STORE_DIR}/" "${RCLONE_REMOTE}/raw/" --immutable

# 4. Retain 14 nightly DB dumps locally; offsite retention managed separately.
ls -1t "${BACKUP_DIR}"/atlascore-*.dump | tail -n +15 | xargs -r rm -f

echo "backup ${STAMP} complete: ${DUMP_FILE}"
```

> If `rsync` is used instead of `rclone`:
> `rsync -a --info=progress2 "${RAW_STORE_DIR}/" offsite-host:/srv/atlascore/raw/`
> and copy the dump with `rsync -a "${DUMP_FILE}" offsite-host:/srv/atlascore/db/`.

### 4.3 systemd timer

`atlascore-backup.service`:

```ini
[Unit]
Description=AtlasCore nightly backup (pg_dump + offsite raw sync)
After=atlascore-stack.service
Requires=atlascore-stack.service

[Service]
Type=oneshot
WorkingDirectory=/opt/atlascore
EnvironmentFile=/opt/atlascore/.env
# Invoke infra/backup.sh (NOT `atlas backup`): the script does the OFFSITE rclone/rsync +
# local retention. `atlas backup` is a local-only snapshot and would skip the offsite copy.
ExecStart=/opt/atlascore/infra/backup.sh
User=atlascore
```

`atlascore-backup.timer`:

```ini
[Unit]
Description=Run AtlasCore backup nightly

[Timer]
OnCalendar=*-*-* 02:30:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

### 4.4 Verifying a backup ran

```bash
journalctl -u atlascore-backup.service --since "yesterday" --no-pager
psql "$DATABASE_URL" -c \
  "SELECT ts, action FROM audit_log WHERE action='backup' ORDER BY ts DESC LIMIT 5;"
rclone lsl offsite:atlascore/db/ | tail -n 5
```

If no backup in > 24h, treat it as an RPO breach: run `infra/backup.sh` manually (offsite) and
investigate the timer/credentials.

---

## 5. Restore Drill (RPO 24h / RTO < 1 day)

**Goal:** Prove the backups are usable. Targets: **RPO 24h** (lose at most one nightly cycle),
**RTO < 1 day** (full recovery within a day). Run this drill at least once after first deploy,
then periodically.

> Drill on a **scratch/standby** target, never the live DB, unless this is a real disaster
> recovery. The drill must restore both Postgres **and** the raw store, then prove that a
> `document` row's `raw_document.path` resolves to a real file on disk.

### 5.1 Restore steps

```bash
# 0. Choose a target. For a drill, use a throwaway DB + temp raw dir.
DRILL_DB="postgresql+psycopg://atlas:***@localhost:5432/atlascore_restore_drill"
DRILL_RAW="/tmp/atlascore-raw-drill"
mkdir -p "$DRILL_RAW"

# 1. Pull the most recent offsite artifacts.
rclone copy offsite:atlascore/db/ /var/restore/db/ --include "atlascore-*.dump"
LATEST_DUMP="$(ls -1t /var/restore/db/atlascore-*.dump | head -n1)"
rclone copy offsite:atlascore/raw/ "$DRILL_RAW/"

# 2. Create the target database and required extensions.
psql "postgresql://atlas:***@localhost:5432/postgres" \
  -c "CREATE DATABASE atlascore_restore_drill;"
psql "$(echo "$DRILL_DB" | sed 's#postgresql+psycopg#postgresql#')" \
  -c "CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pg_trgm;"

# 3. Restore the dump (parallel, ignore extension-already-exists noise).
pg_restore --no-owner --no-privileges --jobs=4 \
  --dbname "$(echo "$DRILL_DB" | sed 's#postgresql+psycopg#postgresql#')" \
  "$LATEST_DUMP"

# 4. Point the app at the restored DB + raw dir for verification only.
export DATABASE_URL="$DRILL_DB"
export RAW_STORE_DIR="$DRILL_RAW"
```

### 5.2 Verification (the drill is not done until these pass)

```bash
# a. Migrations are at head (schema integrity).
make migrate            # should report "nothing to migrate" / no pending revisions

# b. Row counts are sane vs. expectations.
psql "${DATABASE_URL/+psycopg/}" -c \
  "SELECT 'document' t, count(*) FROM document
   UNION ALL SELECT 'raw_document', count(*) FROM raw_document
   UNION ALL SELECT 'source', count(*) FROM source
   UNION ALL SELECT 'time_series_point', count(*) FROM time_series_point;"

# c. Raw-store linkage: there is no `atlas restore verify` CLI. infra/restore_drill.sh restores a
#    dump into a scratch DB and runs sanity counts (incl. an optional raw-blob count); to spot-check
#    by hand, confirm a recent document's raw file resolves on disk:
psql "${DATABASE_URL/+psycopg/}" -c \
  "SELECT d.id, r.path FROM document d JOIN raw_document r ON r.id = d.raw_document_id
   ORDER BY d.ingested_at DESC LIMIT 3;"

# d. Search works (FTS path independent of enrichment).
atlas search "test" --limit 5

# e. A macro series reads back.
psql "${DATABASE_URL/+psycopg/}" -c \
  "SELECT provider, series_id, count(*) FROM time_series_point
   GROUP BY 1,2 ORDER BY 3 DESC LIMIT 5;"
```

`infra/restore_drill.sh` restores the dump into a throwaway scratch DB and runs sanity counts
(including the purge-class audit-retention check and an optional raw-blob count); the manual query
above follows a `document`'s `raw_document_id` to its `raw_document.path` to confirm the gzip file
exists on disk. A missing file means the raw mirror is stale or incomplete — investigate the
offsite raw sync.

### 5.3 Recording the drill

```bash
psql "${DATABASE_URL/+psycopg/}" -c \
  "INSERT INTO audit_log (ts, action, actor, detail)
   VALUES (now(), 'restore_drill', 'cli',
           jsonb_build_object('dump', '${LATEST_DUMP}', 'result', 'pass'));"
```

Record the outcome (PASS/FAIL, dump used, RTO observed) in `docs/STATUS.md` under
"Known issues" if anything failed, and update `CHANGELOG.md` if the procedure changed.

### 5.4 Real disaster recovery (not a drill)

1. Provision a fresh VM per runbook 2 (stack only; do **not** start scheduler/workers yet).
2. Restore Postgres + raw store per 5.1 into the **real** `DATABASE_URL` / `RAW_STORE_DIR`.
3. Run verification 5.2.
4. Start scheduler and workers (runbook 2.3). Confirm exactly one scheduler and one fetch
   worker (runbook 3).
5. Run `atlas health` and the daily digest (runbook 12) to confirm sources resume.

---

## 6. `atlas purge` / Right-to-Erasure (full cascade R3)

**Goal:** Erase a document (and its derived data, including the raw payload) or an entity,
leaving a **retained** audit record that proves erasure without storing the personal data.
This is the single documented exception to raw-store immutability, and it is **idempotent**.

> **Compliance framing (honest):** AtlasCore does not claim to drop all PII — article full
> text inherently contains personal data. The defensible posture rests on data minimization
> plus this working, audited erasure path. The `atlas purge` cascade below is that path; it
> must keep working, and its audit entries are a distinct, **retained** retention class
> (`retention_class='purge'`), never rotated away.

### 6.1 Purge a document (`atlas purge <doc_id>`)

The cascade, in order, in a single transaction where the DB allows (FKs are `NO ACTION` /
`RESTRICT` so this ordering is DB-enforced; array-of-id columns are non-referential and
reconciled by the purge job itself):

1. Delete `embedding` rows where `document_id = <doc_id>`.
2. Delete `mention` rows where `document_id = <doc_id>`.
3. Delete `summary` rows where `document_id = <doc_id>` **OR** `<doc_id> = ANY(member_doc_ids)`.
   Multi-scope summaries set `document_id = NULL` and link only via `member_doc_ids[]`
   (GIN-indexed), so a `document_id`-only delete would miss them and leave purged personal
   data in `summary_text` / `citations`. Affected multi-doc summaries are hard-deleted (or
   recomputed from surviving members later).
4. Remove the document from FTS (drop/clear the `tsvector` row).
5. Delete the `document` row.
6. Delete the `raw_document` **file on disk** (`raw_document.path`) **and** the `raw_document`
   row — **but only when no other `document` still references it** (B14). One raw blob (a whole RSS
   feed / SEC submissions payload) backs **many** documents (one fetch → N per-GUID documents → one
   raw), and the FK is `RESTRICT`, so the raw is erased only when the **last** referencing document
   is purged; until then it is retained (it still holds the purged item's bytes among its siblings).
   This is the one place raw immutability is broken, and only here (`RawStore.delete`, the single
   blob deleter — [ADR 0024](../decisions/0024-b14-purge-cascade-backups-and-restore-drill.md)).
7. Scan `alert.evidence_doc_ids[]` and remove `<doc_id>`. If an alert loses **all** evidence,
   set its `status='purged'`.
8. Write a **retained** `audit_log` entry: `action='purge'`, `actor='cli'`,
   `target_ref=<doc_id>`, `retention_class='purge'`, recording the action and content hashes
   only — **never** the personal data itself.

```bash
atlas purge <doc_id>
```

Idempotency: re-running with the same `<doc_id>` finds nothing to delete and still records (or
recognizes) the erasure; it never errors on already-purged data and never double-counts.

### 6.2 Purge an entity (`atlas purge <entity_id>`)

1. Delete the `mention` rows for that entity.
2. Delete the `entity` row.
3. Documents **survive** (an entity purge does not delete the documents that mentioned it).
4. Write the retained `audit_log` purge entry (`target_ref=<entity_id>`,
   `retention_class='purge'`).

```bash
atlas purge <entity_id>
```

### 6.3 Verification after purge

```bash
# The audit entry exists and is in the retained class.
psql "${DATABASE_URL/+psycopg/}" -c \
  "SELECT ts, action, actor, target_ref, retention_class
   FROM audit_log WHERE target_ref = '<doc_id>' AND action='purge';"

# No derived rows remain.
psql "${DATABASE_URL/+psycopg/}" -c \
  "SELECT
     (SELECT count(*) FROM document      WHERE id='<doc_id>')             AS doc,
     (SELECT count(*) FROM raw_document  WHERE id IN
        (SELECT raw_document_id FROM document WHERE id='<doc_id>'))       AS raw,
     (SELECT count(*) FROM embedding     WHERE document_id='<doc_id>')    AS emb,
     (SELECT count(*) FROM mention       WHERE document_id='<doc_id>')    AS men,
     (SELECT count(*) FROM summary
        WHERE document_id='<doc_id>' OR '<doc_id>' = ANY(member_doc_ids)) AS sums;"

# No alert still references the id; any all-evidence-lost alert is status='purged'.
psql "${DATABASE_URL/+psycopg/}" -c \
  "SELECT id, status FROM alert WHERE '<doc_id>' = ANY(evidence_doc_ids);"

# The raw file is gone from disk (path recorded only in the now-deleted row; check audit hash).
```

> **Regression coverage:** A purge regression test covers the multi-doc summary case — a
> `summary` with `document_id = NULL` linked only through `member_doc_ids[]` must be erased by
> a member-doc purge. This is part of the B14 security/erasure suite.

---

## 6A. Audit-Log Rotation and crawl_job Pruning (B16)

**Goal:** bound the growth of `audit_log` and `crawl_job` without ever destroying erasure proof or
live work. Control-plane, MCP-independent; run on a daily cron now and by the B19 scheduler later.

**The R14 guarantee.** `atlas audit rotate` deletes **only** `audit_log` rows with
`retention_class='routine'` older than the audit window — the filter is on `retention_class`, never
on action strings, so `retention_class='purge'` rows (erasure proof, written by `atlas purge` and by
rotation itself) are **retained forever**. It also prunes **terminal** (`done`/`dead`) `crawl_job`
rows older than the crawl window (keyed on `coalesce(finished_at, created_at)`); `pending`/`claimed`/
`running`/`failed` are never pruned regardless of age.

**Windows (config):** `AUDIT_ROUTINE_RETENTION` (default `180d`) and `CRAWL_JOB_RETENTION`
(default `90d`) — duration grammar `Nd|Nw|Nm|Ny|Nh`, no `permanent`.

```bash
atlas audit rotate --dry-run        # preview counts; changes nothing, writes no bookkeeping entry
atlas audit rotate                  # rotate + prune; writes ONE retained purge-class bookkeeping row
atlas audit rotate --audit-window 365d --crawl-window 30d   # override windows
```

Each non-dry-run writes one retained (`retention_class='purge'`) `action='audit_rotate'` entry with
**counts only** (never row content). Idempotent — re-running deletes nothing new.

**Verify:**

```bash
psql "${DATABASE_URL/+psycopg/}" -c "SELECT retention_class, count(*) FROM audit_log GROUP BY 1;"
psql "${DATABASE_URL/+psycopg/}" -c "SELECT status, count(*) FROM crawl_job GROUP BY 1;"
```

See [ADR 0027](../decisions/0027-audit-rotation-and-crawl-job-retention.md). Cross-refs: §6 (purge —
the other retained-audit path) and §14 (secrets hygiene — `detail` is sanitized before write, so
backups of the audit table are clean).

---

## 6B. Time-Series Partition Management (B17)

**Goal:** keep `time_series_point` carved into **yearly** partitions (with `time_series_point_default`
as the catch-all) so the time-series stays prunable/queryable at volume. Migration 0010 carves the
current + next year at deploy time; this routine keeps the next year ahead.

```bash
atlas tsp partitions status                  # list children + per-child row counts + next-year presence
atlas tsp partitions ensure                  # idempotently carve current + next year (DEFAULT retained)
atlas tsp partitions ensure --years-ahead 2  # carve further ahead
```

`ensure` uses the safe **detach-DEFAULT → create-bounded-child → move-matching-rows → re-attach-DEFAULT**
dance (so it works even when DEFAULT already holds rows for the year), under a dedicated `partition`
advisory lock. It is idempotent. The B19 scheduler runs `worker.partitions.run_partition_maintenance`
as a monthly `job_type=partition` job; until then run it from cron:

```cron
# monthly: keep next-year partition ahead
0 3 1 * *  cd /opt/atlascore && atlas tsp partitions ensure >> /var/log/atlascore/partitions.log 2>&1
```

> systemd alternative: an `atlascore-partitions.timer` (`OnCalendar=monthly`) + oneshot service
> running `atlas tsp partitions ensure`. This is the one routine that *moves* time-series rows
> (raw_document blobs remain immutable). See [ADR 0028](../decisions/0028-b17-time-series-yearly-partitions.md).
>
> **B19 note:** once `atlas scheduler run` is deployed (§6C) it runs partition maintenance inline on
> the `PARTITION_INTERVAL_SECONDS` cadence, so the cron/timer above is only needed if you are not
> running the scheduler.

---

## 6C. Running the Daemons: Scheduler + Worker (B19)

AtlasCore runs unattended from **two** long-lived processes (B19, [ADR 0030](../decisions/0030-b19-scheduler-and-worker-loop.md)):

- **`atlas scheduler run`** — EXACTLY ONE instance (holds the scheduler advisory lock; a second is
  refused). Each tick it enqueues due per-source fetch jobs (cron from each source's
  `config.schedule`, idempotent per tick bucket) and runs freshness/alert/partition/audit
  housekeeping **inline** on their cadences. When it is running you do **not** also need the §6A/§6B
  cron timers.
- **`atlas worker run`** — drains fetch→parse→enrich. Run ONE fetch worker (it holds the single-fetch
  lock, R16); you may add parse/enrich-only workers (`--role parse --role enrich`) to scale out.

Both shut down cleanly on SIGINT/SIGTERM (releasing their locks). Verify with `atlas health` /
`atlas digest` (the *Liveness* line reports scheduler + fetch-worker presence).

```ini
# /etc/systemd/system/atlascore-scheduler.service  (single instance — the invariant)
[Service]
ExecStart=/opt/atlascore/.venv/bin/atlas scheduler run
EnvironmentFile=/etc/atlascore/.env
Restart=on-failure

# /etc/systemd/system/atlascore-worker.service  (single fetch worker — R16)
[Service]
ExecStart=/opt/atlascore/.venv/bin/atlas worker run
EnvironmentFile=/etc/atlascore/.env
Restart=on-failure
```

> **Single-scheduler + single-fetch invariants (§17):** never enable a second scheduler unit or a
> second fetch worker. The advisory locks refuse the second process, but keep the deployment to one
> of each regardless. Cadences are tuned via the `*_INTERVAL_SECONDS` env knobs (`.env.example`).
> Ready-to-install copies of these units (plus the backup/digest timers) ship in
> [`infra/systemd/`](../../infra/systemd/); set `HEARTBEAT_URL` for the scheduler dead-man's-switch.

---

## 7. DLQ Inspection and Requeue

**Goal:** Inspect and recover failed jobs. The job queue is the single `crawl_job` table; jobs
exceeding `max_attempts` move to `status='dead'` (the DLQ).

### 7.1 Inspect the DLQ

```bash
# CLI summary of dead jobs (control-plane surface).
atlas crawl dlq

# Group dead jobs by source and type to spot a systemic failure.
psql "${DATABASE_URL/+psycopg/}" -c \
  "SELECT source_id, job_type, count(*) AS dead, max(finished_at) AS last_dead
   FROM crawl_job WHERE status='dead'
   GROUP BY source_id, job_type ORDER BY dead DESC;"

# Inspect a single dead job's last_error (already secret-sanitized at write time, runbook 14).
# (No `atlas jobs show` CLI in MVP — query the queue directly:)
psql "${DATABASE_URL/+psycopg/}" -c \
  "SELECT id, source_id, job_type, attempt, last_error FROM crawl_job WHERE id='<job_id>';"
```

### 7.2 Diagnose before requeue

Look at `last_error` and `attempt`. Common causes:

| Symptom in `last_error` | Likely cause | Action |
| --- | --- | --- |
| Connect/DNS/read timeout, 5xx, 429 | Transient upstream / rate limit | Safe to requeue after backoff. |
| Parser exception, schema drift | Source format changed | Fix parser, add a fixture, then requeue. |
| 401/403 | Bad/expired credential | Rotate secret (runbook 10), then requeue. |
| 404 / removed | Item deleted upstream | Do **not** requeue; this is expected. |
| SSRF/URL-safety rejection | Disallowed target/redirect | Do **not** requeue; investigate source config. |

### 7.3 Requeue

Requeue resets a dead job to `pending`, zeroes `attempt`, clears `claimed_*`, and lets a worker
re-claim it. Because jobs are idempotent (deterministic `dedupe_key`, content-hash upserts,
`UNIQUE NULLS NOT DISTINCT` on time series, `unique(content_hash)` on raw), replaying never
duplicates data.

Use **`atlas crawl requeue`** (inspect first with `atlas crawl dlq`). It resets dead jobs to
`pending` (attempt 0, cleared `claimed_*`/`last_error`), idempotently and replay-safely:

```bash
atlas crawl requeue <job_id>            # requeue one dead job
atlas crawl requeue --all --yes         # requeue the whole DLQ (--yes required)
atlas crawl requeue --all --type parse --yes   # only dead parse jobs (e.g. after a parser fix)
```

If the CLI is unavailable, the equivalent break-glass SQL is:

```sql
UPDATE crawl_job
SET status='pending', attempt=0, claimed_at=NULL, claimed_by=NULL, last_error=NULL,
    finished_at=NULL, scheduled_at=now()
WHERE id = '<job_id>' AND status='dead';
```

> Re-parsing creates new normalized output (last-write-wins on the `document` row, R2) but
> never mutates the immutable `raw_document`. Re-embedding an unchanged `source_hash` is a
> no-op (R8/R13: `unique(document_id, chunk_index, model_name, model_version)`).

### 7.4 Pruning

`crawl_job` rows in `done`/`dead` are pruned at 90 days by the retention job; do not manually
delete `dead` jobs you have not first inspected and either requeued or accepted.

---

## 8. Stale-Source Handling (cooling_down + digest)

**Goal:** Detect and recover sources that go silently stale or start failing. Freshness is
end-to-end: `source.freshness` thresholds → freshness job → `source_health` →
`get_source_health` (MCP, read-only) → daily digest (CLI-driven).

### 8.1 How staleness is computed

The freshness/health job (`job_type='freshness'`) runs on a schedule and, per source:

- `freshness_lag = now() - coalesce(last_item_published_at, last_success_at)`
- Compares `freshness_lag` against `source.freshness` (`target`, `warn_after`, `error_after`).

`source_health.status` transitions:

| Status | Trigger | Surfaced where |
| --- | --- | --- |
| `ok` | lag ≤ `warn_after`, fetches succeeding | normal |
| `warn` | `warn_after` < lag ≤ `error_after` | daily digest |
| `stale` / `error` | lag > `error_after`, or repeated parse/fetch failure | daily digest + freshness alert |
| `cooling_down` | circuit-broken after N consecutive failures | daily digest; fetches paused |

### 8.2 Triage a stale source

```bash
# See current health for all sources, or one.
atlas health
psql "${DATABASE_URL/+psycopg/}" -c \
  "SELECT source_id, status, freshness_lag, consecutive_failures,
          success_rate_24h, last_success_at, last_item_published_at
   FROM source_health WHERE status <> 'ok' ORDER BY status, freshness_lag DESC;"

# Inspect recent dead jobs (runbook 7; `atlas crawl dlq` lists all — filter by source via psql).
atlas crawl dlq

# Test the source directly (rate-limited, opt-in live).
atlas source test <source_id>
```

### 8.3 Recover

1. If transient (upstream outage), wait for the circuit breaker to half-open, or after the
   upstream recovers, requeue dead fetch jobs (runbook 7.3). The next successful fetch resets
   `consecutive_failures` and moves `status` back toward `ok`.
2. If the feed genuinely moved/changed, update the source YAML (`base_url`, `parser_ref`,
   `fetch.connector`, `fetch.schedule`), re-validate, and re-register (runbook 9).
3. If the source is permanently dead, set `policy_status` appropriately and `enabled=false`
   so it stops being scheduled, and note it in `docs/STATUS.md`.

A `cooling_down` source resumes fetching automatically once the breaker closes after a healthy
probe; you do not normally force it. If you must, requeue a single fetch job and watch
`source_health`.

---

## 9. Onboarding a New Source (default-deny → approved)

**Goal:** Add a source under default-deny policy and only enable it once approved. Policy
states: `approved`, `restricted`, `pending` (disabled), `unknown` (disabled, default),
`prohibited` (never fetched).

> **MVP note:** No HTML/browser source ships in MVP, so no runtime robots gate is built in
> B1–B15. The `policy.robots` field is **policy input / advisory** consulted by the operator
> when setting policy; a runtime robots check is added only inside an HTML/browser connector's
> block if such a source is ever approved. For API/RSS sources, robots is not a runtime gate.

### 9.1 Steps

1. **Write the source YAML** (git-tracked source of truth, mirrored into the `source` table).
   Start with `policy.status: pending` unless you have already confirmed it is approved.
   Use the canonical shape (structured `retention` and `freshness`; `credentials.ref` names an
   env key, never a secret; `fetch.connector`, `fetch.schedule`, `fetch.timeout_seconds`):

   ```yaml
   id: example_rss_feed
   name: Example RSS Feed
   category: news_rss
   access_method: rss
   base_url: https://example.org
   enabled: false
   policy:
     status: pending
     robots: respect
     allowed_uses: [internal_search, internal_summary, agent_retrieval]
   rate_limit: { max_requests: 1, window_seconds: 2 }
   freshness: { target: "24h", warn_after: "36h", error_after: "72h" }
   retention: { raw: "2y", normalized: "permanent" }
   credentials: { ref: null }
   fetch:
     connector: atlascore.connectors.rss.feed:RssConnector
     schedule: "*/15 * * * *"
     timeout_seconds: 30
   ```

   These YAML fields round-trip into the `source` table: `policy.allowed_uses`,
   `policy.robots`, `fetch.connector`, `fetch.schedule`, and `fetch.timeout_seconds` are
   stored in the source's policy/config columns; the scheduler reads the per-source cron
   `schedule` from there.

2. **Validate** the YAML against the schema:

   ```bash
   atlas source validate path/to/example_rss_feed.yaml
   ```

3. **Test** the connector against the live source (rate-limited, opt-in):

   ```bash
   atlas source test example_rss_feed
   ```

   A `pending`/`unknown`/`prohibited` source is refused for real scheduling; `source test`
   exercises the connector without enabling scheduled crawls.

4. **Add fixtures** for success, empty, malformed, rate-limit, source drift, and
   deleted/unavailable, plus a parser golden-output regression test. (Required before enable.)

5. **Approve and enable.** Once you have confirmed the source is `approved` (or `restricted`
   with constraints encoded in the YAML), set `policy.status: approved` and `enabled: true`,
   then re-validate and register:

   ```bash
   atlas source validate path/to/example_rss_feed.yaml
   # register/upsert into the source table (scheduler picks up the schedule)
   atlas source test example_rss_feed
   atlas crawl once example_rss_feed       # one manual crawl to confirm end-to-end
   ```

6. **Confirm health.** After the first scheduled cycle, `source_health` should be `ok` and the
   source should appear in `atlas health` with a sane `freshness_lag`.

7. **Document.** Add a short note in `docs/connectors/README.md`, record the change in
   `CHANGELOG.md [Unreleased]`, and update `docs/STATUS.md`.

### 9.2 Refusal behavior to expect

```bash
$ atlas crawl once some_unknown_source
error: refused — policy_status='unknown' (default-deny). Set policy.status=approved
       (or restricted with encoded constraints) and enabled=true to schedule.
```

---

## 10. Rotating Secrets (one place)

**Goal:** Rotate a credential by editing exactly one place. Sources never store secret values;
they store a `credential_ref` naming an env key resolved at runtime.

### 10.1 The one place

Secrets live in the runtime environment, sourced from `.env` (gitignored) — or, if you use the
optional committed encrypted file, from `secrets.sops.yaml` decrypted at deploy. Source YAML
contains **no** secrets and **no** private endpoints.

```bash
# Edit the single source of secrets.
$EDITOR /opt/atlascore/.env          # update e.g. EMBEDDING_API_KEY / LLM_API_KEY / SMTP_PASS
chmod 600 /opt/atlascore/.env

# Restart the processes that read the rotated key.
sudo systemctl restart atlascore-worker-fetch.service       # if a fetch credential changed
sudo systemctl restart atlascore-worker-proc@*.service      # if an enrichment/LLM key changed
sudo systemctl restart atlascore-scheduler.service          # if a schedule-affecting key changed
```

### 10.2 SOPS/age option (wired — ADR 0036)

The committed-encrypted-secrets option is implemented (`.sops.yaml` + `infra/decrypt-secrets.sh`).
`secrets.sops.yaml` is a **flat YAML map of env vars** encrypted with age — safe to commit; it
decrypts to dotenv (`.env`). Shape:

- `.sops.yaml` (repo root) declares the age recipient — **replace the placeholder with your age
  PUBLIC key** (`age-keygen` prints it). The encrypted `secrets.sops.yaml` is committable.
- `SOPS_AGE_KEY_FILE` points to the age PRIVATE key **outside** the repo (password manager /
  hardware-key-backed file). The key never enters the repo.

```bash
# One-time: generate a key, set its PUBLIC key as the recipient in .sops.yaml, then encrypt .env.
age-keygen -o /secure/keys/atlascore-age.key          # prints "Public key: age1..." -> .sops.yaml
sops -e --input-type dotenv --output-type yaml \
  --filename-override secrets.sops.yaml .env > secrets.sops.yaml   # commit this

# At deploy: materialize .env from the encrypted file (the vetted helper does decrypt + chmod 600).
export SOPS_AGE_KEY_FILE=/secure/keys/atlascore-age.key
infra/decrypt-secrets.sh                               # -> .env (chmod 600)

# Rotate a single value inside the encrypted file, then re-materialize + restart affected services.
sops --set '["EMBEDDING_API_KEY"] "<new-value>"' secrets.sops.yaml
infra/decrypt-secrets.sh
sudo systemctl restart atlascore-worker-proc@*.service
```

> If you prefer the simpler posture, SOPS is optional: declare secrets `.env`-only (gitignored)
> for MVP and skip the encrypted file. Either choice is consistent with "no plaintext secrets
> in the repo." Pick one and record it in `docs/security/README.md`.

### 10.3 After rotation — verify, do not print

- Confirm the affected connector works: `atlas source test <source_id>`.
- Confirm enrichment works if an embedding/LLM key changed: run an enrichment job and check it
  succeeds.
- **Never** echo the resolved secret. Secrets are never printed or logged. Any stored
  free-form field (`last_error`, `payload`, `audit_log.detail`) is sanitized before write
  (runbook 14), so a credential-bearing URL cannot land in the DB or backups.

---

## 11. Embedding Backend Switch (backfill + reindex, R8)

**Goal:** Switch the embedding model/backend or dimension. Because `pgvector`'s `vector(N)`
ANN index requires a fixed dimension, there is exactly **one active embedding model + dim per
deployment**. Switching is a documented, bounded, resumable **backfill + reindex** — never an
online change.

> **Compliance note (cloud backends):** if `EMBEDDING_BACKEND` is `openai` or `google`,
> document text — which can contain personal data — egresses to a third-party (likely
> third-country) processor. For PII-bearing documents either keep a **local** backend, or use
> the cloud backend only under a no-training / zero-retention / DPA configuration and stop
> describing it as a pure household-exemption posture. Decide this before switching and record
> it in `docs/compliance/README.md`.

### 11.1 Procedure

```bash
# 1. Announce/claim the work in docs/STATUS.md (this is an architecture-affecting change).

# 2. Set the new backend config. dim is fixed per deployment.
$EDITOR /opt/atlascore/.env
#   EMBEDDING_BACKEND=openai
#   EMBEDDING_MODEL=text-embedding-3-small
#   EMBEDDING_DIM=1536
#   EMBEDDING_API_KEY=<set if cloud>      # rotate via runbook 10

# 3. Add/confirm a NEW ANN index sized for the new dim, alongside the old one if needed
#    (migration). The embedding table stores model_name/model_version/dim per row, so old and
#    new rows are distinguishable during the transition.

# 4. Backfill: re-embed all documents under the new model. Bounded, observable, resumable (B21).
atlas reindex-embeddings --dry-run                      # preview: counts, target model/dim, no writes
atlas reindex-embeddings --model "$EMBEDDING_MODEL" --batch 100

#    Idempotency: re-embedding an unchanged source_hash is a no-op
#    (unique(document_id, chunk_index, model_name, model_version), R13). Re-runnable / resumable.

# 5. Reindex: build/refresh the HNSW index on the new-dim vectors.
#    (Handled by the backfill's final reindex step / a migration.)

# 6. Cut over reads to the new model_name/model_version (config flag), verify retrieval
#    quality, then drop the old embedding rows + old ANN index in a follow-up migration.
```

### 11.2 Verify

```bash
psql "${DATABASE_URL/+psycopg/}" -c \
  "SELECT model_name, model_version, dim, count(*) FROM embedding
   GROUP BY 1,2,3 ORDER BY 4 DESC;"
# Expect the new (model_name, model_version, dim) to cover all current documents before
# dropping the old set.
```

### 11.3 Rollback

The old embedding rows and index are not dropped until the new set is verified, so rollback is:
flip the read config back to the old `model_name/model_version`, and the old ANN index still
serves queries. Only after a clean cutover do you drop the old rows.

> Graceful degradation: FTS search keeps working even while the vector index is being rebuilt
> (search works without enrichment; FTS works if vector search is down).

---

## 12. Daily Digest and Health (`atlas health` / `atlas digest`)

**Goal:** A once-daily operational summary, plus an on-demand health snapshot. Health is
**CLI-driven and control-plane** (MCP-independent). The MCP `get_source_health` tool is a
read-only convenience for agents, **not** the operational gate — the architecture rule forbids
the control plane depending on MCP.

### 12.1 What the digest contains

`atlas digest` reads `source_health` and the `crawl_job` DLQ depth, plus a scheduler/worker
liveness check, and emits a summary over the alert delivery channel
(`ALERT_WEBHOOK_URL` / `NTFY_TOPIC` / SMTP):

- Sources in `warn` / `stale` / `error` / `cooling_down` with their `freshness_lag`.
- DLQ depth (`crawl_job` rows with `status='dead'`), grouped by source/type.
- New alerts in the last 24h (count by `alert_type`/severity).
- Liveness: exactly-one-scheduler check and fetch-worker presence.

```bash
# On-demand health snapshot (no delivery; prints to the operator's terminal).
atlas health

# Build and deliver the daily digest via the configured channel.
atlas digest
```

`atlas health` output combines `source_health`, DLQ depth, and the single-instance checks from
runbook 3, so it is the operational gate when something looks wrong.

### 12.2 systemd timer

`atlascore-digest.service`:

```ini
[Unit]
Description=AtlasCore daily digest (source health + DLQ + liveness -> alert channel)
After=atlascore-stack.service
Requires=atlascore-stack.service

[Service]
Type=oneshot
WorkingDirectory=/opt/atlascore
EnvironmentFile=/opt/atlascore/.env
ExecStart=/opt/atlascore/.venv/bin/atlas digest
User=atlascore
```

`atlascore-digest.timer`:

```ini
[Unit]
Description=Send the AtlasCore daily digest

[Timer]
OnCalendar=*-*-* 07:00:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

### 12.3 If the digest does not arrive

1. Check the timer/service: `journalctl -u atlascore-digest.service --since today --no-pager`.
2. Check the delivery channel env (`ALERT_WEBHOOK_URL`/`NTFY_TOPIC`/SMTP) and run
   `atlas digest` manually.
3. A *missing* digest is itself a signal the box may be down — confirm the VM, Postgres, and
   the scheduler/fetch worker are alive (runbook 3).

---

## 13. Reclaiming Stuck Claimed Jobs (visibility timeout)

**Goal:** Recover jobs left in `claimed`/`running` by a crashed or killed worker. The model:
workers claim via `SELECT ... FOR UPDATE SKIP LOCKED`; a claimed-but-stale job (where
`claimed_at` is older than the visibility timeout) is reclaimed to `pending` automatically.

### 13.1 Detect stuck jobs

```bash
# No `atlas jobs stuck` CLI in MVP — the running worker loop auto-reclaims stale jobs; list them
# directly for inspection:
psql "${DATABASE_URL/+psycopg/}" -c \
  "SELECT id, source_id, job_type, status, claimed_by, claimed_at, attempt
   FROM crawl_job
   WHERE status IN ('claimed','running')
     AND claimed_at < now() - interval '15 minutes'
   ORDER BY claimed_at;"
```

(15 minutes is the example visibility timeout; the real value is the configured timeout.)

### 13.2 Reclaim

The running worker loop reclaims stale `claimed`/`running` jobs automatically every
`visibility_timeout/2` (crash safety) — normally you do nothing. There is no `atlas jobs reclaim`
CLI in MVP; if the worker is down, force it with the break-glass SQL:

```sql
UPDATE crawl_job
SET status='pending', claimed_at=NULL, claimed_by=NULL
WHERE status IN ('claimed','running')
  AND claimed_at < now() - interval '15 minutes';
```

Reclaimed jobs are safe to re-run because jobs are idempotent (deterministic `dedupe_key`,
content-hash upserts, unique constraints prevent duplicates). A job that has already exhausted
`max_attempts` goes to `dead` (DLQ) rather than being reclaimed indefinitely; inspect those via
runbook 7.

### 13.3 If a worker is wedged but its PID is alive

A live-but-stuck worker still holds nothing in the DB (claims are short transactions), so the
visibility-timeout reclaim covers it. Restart the worker to clear the wedge:

```bash
sudo systemctl restart atlascore-worker-fetch.service        # or atlascore-worker-proc@N
```

---

## 14. Secrets Hygiene in Stored Free-Form Fields

**Goal:** Keep secrets out of the DB and therefore out of backups. Free-form sinks
(`crawl_job.last_error`, `crawl_job.payload`, `audit_log.detail`) can otherwise capture
`api_key=...` URLs (FRED uses `?api_key=`) or `Authorization`/`Cookie` headers, which the
repo-only secret scanners would never catch.

### 14.1 Invariant

Before persisting `crawl_job.last_error`, `crawl_job.payload`, or `audit_log.detail`, the
single `sanitize_for_storage` helper in `packages/security/secrets`:

- Redacts known-secret patterns.
- Strips URL **query** strings and **userinfo**.
- Removes `Authorization` and `Cookie` headers.

Storing a resolved credential value or a credential-bearing URL in any jsonb/text column is
forbidden. Backups inherit this protection (runbook 4).

### 14.2 Operational checks

```bash
# Spot-check that no obvious secret pattern landed in free-form sinks (should return 0 rows).
psql "${DATABASE_URL/+psycopg/}" -c \
  "SELECT id FROM crawl_job
   WHERE last_error ~* 'api_key=|authorization:|cookie:|bearer '
   LIMIT 20;"
psql "${DATABASE_URL/+psycopg/}" -c \
  "SELECT id FROM audit_log
   WHERE detail::text ~* 'api_key=|authorization|cookie|bearer '
   LIMIT 20;"
```

If either returns rows, treat it as a secrets incident: rotate the exposed credential
(runbook 10), purge/redact the offending rows, and fix the sink that bypassed
`sanitize_for_storage`. The B15 secrets-in-logs regression test covers `last_error` and
`audit_log.detail`; add a case if a new sink slipped through.

---

## 15. Runbook Index Summary

| Runbook | When to use | Primary command(s) |
| --- | --- | --- |
| 2 Deploy | New VM / DR rebuild | `make migrate`, `systemctl enable --now …` |
| 3 Multi-worker | Verify one scheduler / one fetch worker | `pgrep`, `systemctl status`, advisory-lock check |
| 4 Backup | Nightly (auto) or manual | `infra/backup.sh` (offsite); `atlas backup` (local snapshot) |
| 5 Restore drill | Periodically; real DR | `pg_restore`, `infra/restore_drill.sh` |
| 6 Purge / erasure | Erase a doc or entity | `atlas purge <id>` |
| 7 DLQ | Failed jobs piling up | `atlas crawl dlq`, `atlas crawl requeue` |
| 8 Stale source | A feed went silent | `atlas health`, `atlas source test` |
| 9 Onboard source | Add a new source | `atlas source validate/test`, `atlas crawl once` |
| 10 Rotate secrets | Credential change | edit `.env` (one place), restart services |
| 11 Embedding switch | Change model/dim/backend | `atlas reindex-embeddings` |
| 12 Digest / health | Daily summary; triage | `atlas health`, `atlas digest` |
| 13 Stuck jobs | Worker crash recovery | auto-reclaim in `atlas worker run`; break-glass SQL |
| 14 Secrets hygiene | Audit free-form fields | psql spot-checks; `sanitize_for_storage` |

---

### Maintenance

Update this file in the **same change** as any behavior, schema, MCP, security, or compliance
change that affects operations. Keep it consistent with the AtlasCore Canonical Decisions Spec
v1; if the spec and this file disagree, the spec wins — fix the runbook. Record operationally
relevant changes in `CHANGELOG.md [Unreleased]`, note in-progress operational work in
`docs/STATUS.md`, and add an ADR under `docs/decisions/` for any architecture-changing
operational decision (e.g. enabling the multi-fetch-worker `host_rate_state` scale-up).

*Document date: 2026-06-03.*
