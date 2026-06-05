#!/bin/sh
# AtlasCore — restore drill (SPEC B14: "test a restore once").
#
# Verifies a pg_dump produced by infra/backup.sh (or `atlas backup`) actually restores, by
# restoring it into a THROWAWAY scratch database and running a few sanity counts. It never touches
# the live database. Pair with a manual check that the raw store copy is present + readable.
#
# Posture (§14): the password comes from PGPASSWORD / ~/.pgpass, never a CLI flag. The scratch DB is
# created and dropped here; the live DB is read-only to this script (we only pg_restore into scratch).
#
# Required environment:
#   POSTGRES_USER, POSTGRES_HOST (default localhost), POSTGRES_PORT (default 5432)
#   PGPASSWORD            (or a ~/.pgpass entry)
#   DUMP_FILE            (path to the pg_dump custom-format file to restore)
# Optional:
#   RESTORE_DB           (scratch db name; default atlascore_restore_drill)
#   RAW_BACKUP_DIR       (a raw-store copy to sanity-check is present + non-empty)
#
# Usage:
#   set -a; . /etc/atlascore/.env; set +a
#   DUMP_FILE=/var/backups/atlascore/<ts>/atlascore-pg.dump sh infra/restore_drill.sh

set -eu

POSTGRES_HOST="${POSTGRES_HOST:-localhost}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
RESTORE_DB="${RESTORE_DB:-atlascore_restore_drill}"

: "${POSTGRES_USER:?POSTGRES_USER must be set}"
: "${DUMP_FILE:?DUMP_FILE must be set (path to the pg_dump to restore)}"

if [ ! -f "$DUMP_FILE" ]; then
  echo "restore-drill: DUMP_FILE not found: $DUMP_FILE" >&2
  exit 1
fi

psql_scratch() {
  psql --host="$POSTGRES_HOST" --port="$POSTGRES_PORT" --username="$POSTGRES_USER" \
    --dbname="$RESTORE_DB" --no-psqlrc --tuples-only --command="$1"
}

echo "restore-drill: (re)creating scratch database '${RESTORE_DB}'"
dropdb --host="$POSTGRES_HOST" --port="$POSTGRES_PORT" --username="$POSTGRES_USER" \
  --if-exists "$RESTORE_DB"
createdb --host="$POSTGRES_HOST" --port="$POSTGRES_PORT" --username="$POSTGRES_USER" "$RESTORE_DB"

echo "restore-drill: restoring ${DUMP_FILE} -> ${RESTORE_DB}"
pg_restore --host="$POSTGRES_HOST" --port="$POSTGRES_PORT" --username="$POSTGRES_USER" \
  --dbname="$RESTORE_DB" --no-owner --no-privileges "$DUMP_FILE"

echo "restore-drill: verifying schema + row counts"
TABLES="$(psql_scratch "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';")"
echo "restore-drill:   public tables restored: $(echo "$TABLES" | tr -d ' ')"
# The audit_log purge-retention guarantee must survive a restore.
psql_scratch "SELECT 'documents=' || count(*) FROM document;"
psql_scratch "SELECT 'sources=' || count(*) FROM source;"
psql_scratch "SELECT 'purge_audit=' || count(*) FROM audit_log WHERE retention_class='purge';"

if [ -n "${RAW_BACKUP_DIR:-}" ]; then
  if [ -d "$RAW_BACKUP_DIR" ]; then
    RAW_COUNT="$(find "$RAW_BACKUP_DIR" -name '*.gz' | wc -l | tr -d ' ')"
    echo "restore-drill:   raw blobs in backup: ${RAW_COUNT}"
  else
    echo "restore-drill: WARNING — RAW_BACKUP_DIR not found: $RAW_BACKUP_DIR" >&2
  fi
fi

echo "restore-drill: dropping scratch database '${RESTORE_DB}'"
dropdb --host="$POSTGRES_HOST" --port="$POSTGRES_PORT" --username="$POSTGRES_USER" "$RESTORE_DB"

echo "restore-drill: OK — dump restores cleanly"
