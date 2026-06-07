#!/bin/sh
# launchd wrapper: the single-instance scheduler. Self-locates the soak root (this file lives at
# infra/launchd/bin/), cd's there so the `atlas` CLI (pydantic-settings) auto-loads ./.env, and execs
# the venv entrypoint. No .env sourcing needed here — pydantic reads it from the working directory.
set -eu
SOAK_DIR="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$SOAK_DIR"
exec .venv/bin/atlas scheduler run
