#!/bin/sh
# launchd wrapper: nightly backup (oneshot, driven by StartCalendarInterval). Unlike the atlas CLI,
# infra/backup.sh is a plain shell script and does NOT auto-load .env — and it needs pg_dump + rclone,
# which launchd's minimal PATH lacks. So this wrapper: (1) loads .env robustly (values may contain
# spaces/parens, so we do NOT `. ./.env`), (2) maps POSTGRES_PASSWORD -> PGPASSWORD and sets a local
# BACKUP_DIR, (3) prepends Homebrew bin dirs for pg_dump (libpq) + rclone, then runs the backup.
set -eu
SOAK_DIR="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$SOAK_DIR"

# Robust .env loader: split on the FIRST '=', keep the rest verbatim, skip blanks/comments.
if [ -f .env ]; then
  while IFS='=' read -r key val; do
    case "$key" in ''|\#*) continue ;; esac
    export "$key=$val"
  done < .env
fi

export PGPASSWORD="${PGPASSWORD:-${POSTGRES_PASSWORD:-}}"
export BACKUP_DIR="${BACKUP_DIR:-$SOAK_DIR/var/backups}"
# Homebrew locations for pg_dump (libpq) + rclone; launchd starts with a bare PATH.
export PATH="/opt/homebrew/bin:/opt/homebrew/opt/libpq/bin:/usr/local/bin:${PATH}"

exec ./infra/backup.sh
