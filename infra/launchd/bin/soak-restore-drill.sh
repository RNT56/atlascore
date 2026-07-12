#!/bin/sh
set -eu

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
set -a
. "$ROOT/.env"
set +a
export PATH="/opt/homebrew/opt/libpq/bin:/usr/local/opt/libpq/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
export BACKUP_DIR="${BACKUP_DIR:-$ROOT/var/backups}"
export POSTGRES_HOST="${POSTGRES_HOST:-localhost}"
export POSTGRES_PORT="${POSTGRES_PORT:-5432}"
export PGPASSWORD="${PGPASSWORD:-${POSTGRES_PASSWORD:-}}"
exec "$ROOT/infra/restore-latest.sh"
