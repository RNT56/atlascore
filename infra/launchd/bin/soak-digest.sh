#!/bin/sh
# launchd wrapper: the daily digest (oneshot, driven by StartCalendarInterval in the plist).
# Emits over the configured channel (ntfy/webhook/SMTP) and pings HEARTBEAT_URL; reads ./.env via pydantic.
set -eu
SOAK_DIR="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$SOAK_DIR"
exec .venv/bin/atlas digest
