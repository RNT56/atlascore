#!/bin/sh
# Materialize .env from the committed, age-encrypted secrets.sops.yaml (OPTIONAL SOPS path; §10.2).
#
# secrets.sops.yaml is an encrypted FLAT YAML map of env vars; we decrypt it to dotenv (.env) so the
# `atlas` CLI / Docker Compose read it normally. The encrypted file is created with:
#   sops -e --input-type dotenv --output-type yaml .env > secrets.sops.yaml
#
# Requires: sops on PATH (`brew install sops`) and SOPS_AGE_KEY_FILE pointing at your age PRIVATE key
# OUTSIDE the repo (the key is never committed). Run from anywhere; resolves the repo root itself.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

: "${SOPS_AGE_KEY_FILE:?set SOPS_AGE_KEY_FILE to your age private key path (OUTSIDE the repo)}"
command -v sops >/dev/null 2>&1 || { echo "error: sops not installed (brew install sops)" >&2; exit 1; }
[ -f secrets.sops.yaml ] || {
  echo "error: secrets.sops.yaml not found — create it once with:" >&2
  echo "       sops -e --input-type dotenv --output-type yaml .env > secrets.sops.yaml" >&2
  exit 1
}

umask 077
sops -d --input-type yaml --output-type dotenv secrets.sops.yaml > .env
chmod 600 .env
echo "decrypted secrets.sops.yaml -> .env (chmod 600)"
