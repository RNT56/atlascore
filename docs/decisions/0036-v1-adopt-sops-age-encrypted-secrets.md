# 0036. V1: wire the optional SOPS/age encrypted-secrets path

- **Status:** Accepted
- **Date:** 2026-06-07
- **Deciders:** Operator (single-operator project)
- **Spec reference:** AtlasCore Canonical Decisions Spec v1 (date 2026-06-03), §3/§12/§16 (secrets never in the repo; an OPTIONAL age/SOPS-encrypted file MAY be committed with the decrypt key OUTSIDE the repo), roadmap §15.2 (v1: "optionally adopt SOPS/age")

## Context

Secrets are `.env`-only (gitignored) today, resolved from the environment at runtime — correct and sufficient for the single-box MVP/soak. The spec also allows an OPTIONAL committed, age-encrypted secrets file so the operator can keep an encrypted copy in the repo (handy for the forthcoming Hetzner move and for disaster recovery), with the decrypt key living outside the repo. The `.gitignore` already permits a committed `secrets.sops.yaml`, `core.settings` already carries `sops_age_key_file`, and the runbook described the shape — but the actual `.sops.yaml` config and a vetted decrypt step did not exist, and the documented `sops -d secrets.sops.yaml > .env` had a format mismatch (a YAML-named file decrypted straight to a dotenv `.env`).

This is a **freeze-compatible** v1 increment: it adds **no source and no service**, so the §2.5/§26 scope freeze (which gates new sources/services until the current set runs unattended a month) does not apply. It is opt-in and changes nothing for operators who stay `.env`-only.

## Decision

Wire the SOPS/age path as an **optional** secrets posture:

- **`.sops.yaml`** (repo root) — a `creation_rules` config with `path_regex: ^secrets\.sops\.yaml$` and a clearly-invalid placeholder age recipient the operator replaces with their age PUBLIC key. Committable; safe (a public key, not a secret).
- **`secrets.sops.yaml`** — a **flat YAML map of env vars**, age-encrypted, committable. Created with `sops -e --input-type dotenv --output-type yaml --filename-override secrets.sops.yaml .env > secrets.sops.yaml` (the `--filename-override` makes the creation rule match while reading dotenv `.env`).
- **`infra/decrypt-secrets.sh`** — the vetted deploy/rotate step: requires `SOPS_AGE_KEY_FILE` (private key OUTSIDE the repo) + `sops`, then `sops -d --input-type yaml --output-type dotenv secrets.sops.yaml > .env` and `chmod 600`. The `--output-type dotenv` is the fix for the YAML→dotenv format mismatch.
- Runbook §10.2 (and the §2 deploy comment) reconciled to these exact commands + the helper.

The decrypt key never enters the repo; `.env` stays gitignored; `secrets.sops.yaml`/`.sops.yaml` are the only secrets-adjacent files that may be committed (encrypted / public-key-only). Validated end-to-end with a throwaway age key (encrypt → `infra/decrypt-secrets.sh` → a correct 51-line `.env`; ciphertext hides the values).

## Consequences

- **Positive:** the operator can keep an encrypted secrets copy in the repo (DR + Hetzner-move convenience) with the key outside it; one vetted command (`infra/decrypt-secrets.sh`) does deploy + rotation re-materialization; the runbook commands are now correct (they previously produced malformed `.env`).
- **Negative / costs:** SOPS + age are extra operator tooling (`brew install sops age`); a placeholder `.sops.yaml` is non-functional until the operator sets their real recipient (intentional — it refuses to encrypt otherwise); no automated test (it is shell/binary tooling — validated manually with a throwaway key, like the other `infra/*.sh` scripts).
- **Follow-ups / obligations:** record the chosen posture (`.env`-only vs SOPS) in `docs/security/README.md`; on the Hetzner move, generate the real age key and encrypt `secrets.sops.yaml`.
- **Revisit triggers:** multi-operator key management (would need multiple recipients / a KMS), or moving secrets to a managed secret store (would supersede this).
