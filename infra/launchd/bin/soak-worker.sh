#!/bin/sh
# launchd wrapper: the single fetch worker (drains fetch->parse->enrich; holds the fetch lock, R16).
# Self-locates the soak root and execs the venv entrypoint; `atlas` reads ./.env via pydantic.
set -eu
SOAK_DIR="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$SOAK_DIR"
exec .venv/bin/atlas worker run
