#!/bin/sh
# Install the AtlasCore soak LaunchAgents on macOS (the Mac-mini equivalent of infra/systemd/).
#
# Usage:  infra/launchd/install.sh [SOAK_DIR]
#   SOAK_DIR defaults to the repo root that contains this script (i.e. run it from the soak clone).
#
# It substitutes __SOAK_DIR__ into the four .plist.template files, writes them to
# ~/Library/LaunchAgents, and (re)loads them. Scheduler + worker are always-on (KeepAlive); digest +
# backup fire on a daily StartCalendarInterval (LOCAL time). Prereqs: `.env` filled in, `make setup`
# run (so .venv exists), the Postgres stack up (`make dev && make migrate && make seed`), and — for
# backups — `pg_dump`/`rclone` installed (e.g. `brew install libpq rclone`).
set -eu

SOAK_DIR="${1:-$(cd "$(dirname "$0")/../.." && pwd)}"
LA="$HOME/Library/LaunchAgents"

[ -d "$SOAK_DIR/.venv" ] || { echo "error: $SOAK_DIR/.venv missing — run 'make setup' first" >&2; exit 1; }
[ -f "$SOAK_DIR/.env" ]  || { echo "error: $SOAK_DIR/.env missing — cp .env.example .env and fill it" >&2; exit 1; }

mkdir -p "$LA" "$SOAK_DIR/var/log" "$SOAK_DIR/var/backups" "$SOAK_DIR/var/raw"

for tmpl in "$SOAK_DIR"/infra/launchd/*.plist.template; do
  name="$(basename "$tmpl" .template)"
  dest="$LA/$name"
  sed "s#__SOAK_DIR__#$SOAK_DIR#g" "$tmpl" > "$dest"
  launchctl unload "$dest" 2>/dev/null || true
  launchctl load -w "$dest"
  echo "loaded $name"
done

echo
echo "Done. Verify:"
echo "  pgrep -af 'atlas scheduler run' | wc -l    # expect 1"
echo "  pgrep -af 'atlas worker run'    | wc -l    # expect 1"
echo "  (cd '$SOAK_DIR' && .venv/bin/atlas health)"
