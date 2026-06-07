#!/bin/sh
# Unload + remove the AtlasCore soak LaunchAgents (clean teardown after the soak / before a redeploy).
set -eu
LA="$HOME/Library/LaunchAgents"
for name in scheduler worker digest backup; do
  plist="$LA/com.atlascore.soak.$name.plist"
  if [ -f "$plist" ]; then
    launchctl unload "$plist" 2>/dev/null || true
    rm -f "$plist"
    echo "removed com.atlascore.soak.$name"
  fi
done
echo "Done. (The Postgres stack + data volume are untouched: 'make down' / 'docker compose ... down -v' separately.)"
