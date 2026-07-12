#!/bin/sh
# Restore the newest local AtlasCore dump into the disposable drill database.
set -eu

: "${BACKUP_DIR:?BACKUP_DIR must be set}"

# Support canonical infra/backup.sh dumps and atlas backup's timestamp directories.
DUMP_FILE="$(find "$BACKUP_DIR" -type f \( -name 'atlascore-pg-*.dump' -o -name 'atlascore-pg.dump' \) -print 2>/dev/null | sort | tail -1)"
if [ -z "$DUMP_FILE" ]; then
  echo "restore-latest: no AtlasCore dump found under $BACKUP_DIR" >&2
  exit 1
fi

if [ -z "${PGPASSWORD:-}" ] && [ -n "${POSTGRES_PASSWORD:-}" ]; then
  export PGPASSWORD="$POSTGRES_PASSWORD"
fi
export DUMP_FILE
exec "$(dirname "$0")/restore_drill.sh"
