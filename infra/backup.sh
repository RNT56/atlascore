#!/bin/sh
# AtlasCore — nightly backup (date: 2026-06-03)
#
# Backs up the two durable stores of the single-box deployment:
#   1. PostgreSQL control/normalized plane  -> pg_dump (custom format, compressed).
#   2. Immutable content-addressed raw store -> mirrored offsite with rclone (or rsync).
#
# Posture (SPEC §12 storage, §14 security, item (8) sanitize-for-storage):
#   - No secrets are inlined. Everything sensitive is read from the environment / a gitignored
#     .env you `set -a; . ./.env; set +a` before invoking, or your systemd unit's EnvironmentFile.
#   - Backups inherit the in-database redaction guarantee: the app must never persist resolved
#     credentials or credential-bearing URLs (e.g. ?api_key=...) into last_error / payload /
#     audit_log.detail (handled by packages/security/secrets:sanitize_for_storage). This script
#     does not — and must not — try to scrub dumps after the fact; the data must already be clean.
#   - pg_dump password comes from PGPASSWORD or a ~/.pgpass file, never a CLI flag.
#
# Restore hint (one line):
#   pg_restore --clean --if-exists --no-owner -d "$ATLAS_RESTORE_DB" /path/to/atlascore-pg-YYYYmmddTHHMMSSZ.dump
#   (then re-sync the raw store dir back from offsite). Test a restore at least once — SPEC B14.
#
# Required environment:
#   POSTGRES_USER, POSTGRES_DB, POSTGRES_HOST (default localhost), POSTGRES_PORT (default 5432)
#   PGPASSWORD               (or a ~/.pgpass entry; NEVER passed on the command line)
#   RAW_STORE_DIR            (local immutable raw store to mirror)
#   BACKUP_DIR               (local dir to write dumps into; should itself be on durable disk)
#   BACKUP_OFFSITE_REMOTE    (rclone remote target, e.g. "remote:atlascore" — preferred), OR
#   BACKUP_OFFSITE_RSYNC     (rsync target, e.g. "user@host:/srv/atlascore-backups" — fallback)
# Optional:
#   BACKUP_RETAIN            (number of pg dumps to retain locally; default 14)
#
# Usage:
#   set -a; . /etc/atlascore/.env; set +a
#   sh infra/backup.sh

set -euo pipefail

# --- Config (env-driven; no secrets inline) ---------------------------------
POSTGRES_HOST="${POSTGRES_HOST:-localhost}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
BACKUP_RETAIN="${BACKUP_RETAIN:-14}"

# --- Required-variable guards -----------------------------------------------
: "${POSTGRES_USER:?POSTGRES_USER must be set}"
: "${POSTGRES_DB:?POSTGRES_DB must be set}"
: "${RAW_STORE_DIR:?RAW_STORE_DIR must be set (path to immutable raw store)}"
: "${BACKUP_DIR:?BACKUP_DIR must be set (local dir for pg dumps)}"

if [ ! -d "$RAW_STORE_DIR" ]; then
  echo "backup: RAW_STORE_DIR does not exist: $RAW_STORE_DIR" >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"

# UTC timestamp; sortable; matches AtlasCore's UTC-everywhere rule.
TS="$(date -u +%Y%m%dT%H%M%SZ)"
DUMP_FILE="${BACKUP_DIR}/atlascore-pg-${TS}.dump"

# --- 1. PostgreSQL dump (custom format, compressed, restorable selectively) --
echo "backup: dumping database '${POSTGRES_DB}' -> ${DUMP_FILE}"
pg_dump \
  --host="$POSTGRES_HOST" \
  --port="$POSTGRES_PORT" \
  --username="$POSTGRES_USER" \
  --dbname="$POSTGRES_DB" \
  --format=custom \
  --compress=9 \
  --no-owner \
  --no-privileges \
  --file="$DUMP_FILE"

echo "backup: pg dump complete ($(wc -c < "$DUMP_FILE") bytes)"

# --- 2. Offsite sync: pg dumps + immutable raw store ------------------------
# rclone is preferred (cloud-object stores); rsync is the fallback for an SSH target.
if [ -n "${BACKUP_OFFSITE_REMOTE:-}" ]; then
  echo "backup: syncing dumps + raw store to rclone remote '${BACKUP_OFFSITE_REMOTE}'"
  # Dumps directory (additive copy; old offsite dumps pruned by remote lifecycle/your policy).
  rclone copy "$BACKUP_DIR" "${BACKUP_OFFSITE_REMOTE}/pg" --transfers=4 --checksum
  # Raw store is immutable + content-addressed: a one-way mirror is safe and cheap.
  rclone sync "$RAW_STORE_DIR" "${BACKUP_OFFSITE_REMOTE}/raw" --transfers=4 --checksum
elif [ -n "${BACKUP_OFFSITE_RSYNC:-}" ]; then
  echo "backup: syncing dumps + raw store to rsync target '${BACKUP_OFFSITE_RSYNC}'"
  rsync -az --partial "$BACKUP_DIR"/ "${BACKUP_OFFSITE_RSYNC}/pg/"
  # --delete is intentional ONLY for the immutable raw mirror to track local prunes/retention.
  rsync -az --partial --delete "$RAW_STORE_DIR"/ "${BACKUP_OFFSITE_RSYNC}/raw/"
else
  echo "backup: WARNING — neither BACKUP_OFFSITE_REMOTE nor BACKUP_OFFSITE_RSYNC set; offsite sync skipped" >&2
fi

# --- 3. Local retention: keep newest N pg dumps -----------------------------
echo "backup: pruning local pg dumps, retaining newest ${BACKUP_RETAIN}"
# List dumps newest-first, skip the first N, delete the rest. Filename pattern is fixed above.
ls -1t "${BACKUP_DIR}"/atlascore-pg-*.dump 2>/dev/null \
  | tail -n "+$((BACKUP_RETAIN + 1))" \
  | while IFS= read -r old; do
      echo "backup: removing old dump $old"
      rm -f "$old"
    done

echo "backup: done at ${TS}"
echo "backup: restore with -> pg_restore --clean --if-exists --no-owner -d \"\$ATLAS_RESTORE_DB\" ${DUMP_FILE}"
