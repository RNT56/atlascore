# Getting Started with AtlasCore

This guide takes a new operator from an empty machine to a working local AtlasCore instance, a
first governed ingestion, and the optional read surfaces. It uses the released single-box shape:
Python managed by `uv`, PostgreSQL 16 + pgvector in Docker Compose, and application processes on the
host.

For an unattended Linux or macOS deployment, finish this guide first and then continue with the
[operational runbooks](runbooks/README.md).

## 1. Prerequisites

Install:

- Git.
- Python 3.12 or 3.13.
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/).
- Docker Desktop, or Docker Engine with the Compose plugin.

Verify the tools before cloning:

```bash
git --version
uv --version
docker version
docker compose version
```

`docker version` must show a reachable server/daemon, not only the client. AtlasCore's unit tests can
run without Docker, but migrations, integration tests, ingestion, search, and the read surfaces need
PostgreSQL.

## 2. Clone and install

Clone the current protected default branch for local evaluation and development:

```bash
git clone https://github.com/RNT56/atlascore.git
cd atlascore
make setup
```

For a reproducible unattended deployment, pin the latest release as shown in the production
[runbook](runbooks/README.md#22-first-deploy). `make setup` creates `.venv`, installs the locked
project plus development tools, creates `.env` from `.env.example` if needed, and installs the Git
hooks.

AtlasCore does not require activating the virtual environment. Run application commands as `uv run
atlas ...`; this always uses the project environment and committed lockfile.

## 3. Configure the local environment

Open the generated `.env` and review these values:

| Setting | Local requirement | Production requirement |
|---|---|---|
| `APP_ENV` | Keep `dev`. | Set `prod`. |
| `POSTGRES_*` and `DATABASE_URL` | The shipped local values work together. | Replace the password in both places with the same strong secret. |
| `RAW_STORE_DIR` | The default `./var/rawstore` is writable and gitignored. | Use an absolute, persistent, backed-up path. |
| `DEFAULT_USER_AGENT` | Replace `you@example.com` with a reachable contact before live ingestion. | Required; do not impersonate a browser. |
| `FRED_API_KEY` | Only needed for enabled FRED sources. | Resolve all source credential refs from the environment. |
| Delivery, heartbeat, backup settings | Optional for local exploration. | Required by the soak preflight. |

The committed source registry contains credential references, never secret values. Keep `.env`, raw
payloads, database volumes, and backups out of Git.

## 4. Start PostgreSQL and initialize AtlasCore

```bash
make dev
make migrate
make seed
```

Then verify the installation and registry:

```bash
uv run atlas --version
uv run atlas source list
uv run atlas health
```

Expected results:

- `atlas --version` reports `atlascore 0.1.0` for the tagged release.
- `source list` shows the registered source slugs and their policy state.
- `health` reaches PostgreSQL. Before the scheduler has run, sources may legitimately have no health
  history yet.

`make seed` is idempotent: it validates every YAML file before writing and then upserts the registry.

## 5. Run a first ingestion

The worker is a long-running process. Start it in terminal A:

```bash
uv run atlas worker run
```

In terminal B, enqueue a keyless approved source:

```bash
uv run atlas crawl enqueue fed_press_releases
```

The worker drains fetch → parse → enrich. After it reports the work and becomes idle, query the
processed store:

```bash
uv run atlas search "Federal Reserve"
uv run atlas health
uv run atlas crawl dlq
```

Stop the development worker with `Ctrl-C`. This first ingestion uses a real public feed and therefore
needs network access and a real `DEFAULT_USER_AGENT` contact. If you want a deterministic offline
proof instead, run the smoke test after PostgreSQL is ready:

```bash
make smoke
```

## 6. Run continuously

A continuous installation needs exactly one scheduler and one fetch-capable worker:

```bash
# terminal/process 1 — exactly one
uv run atlas scheduler run

# terminal/process 2 — exactly one fetch-capable worker
uv run atlas worker run
```

Do not manage an unattended deployment with terminal sessions. Use the vetted
[`infra/systemd`](../infra/systemd/README.md) units on Linux or
[`infra/launchd`](../infra/launchd/README.md) agents on macOS. Both preserve the single-scheduler and
single-fetch-worker invariants and include backup, digest, and restore-drill timers.

## 7. Use the read surfaces

### CLI

The CLI is the operator control plane:

```bash
uv run atlas --help
uv run atlas search "query"
uv run atlas alerts list
uv run atlas summarize <document-id> --cache-only
```

Use `uv run atlas <command> --help` for the exact arguments and safety confirmations.

### WebUI and JSON API

Install the optional web dependencies and bind to loopback:

```bash
uv sync --extra web
uv run uvicorn atlascore.api.app:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000/>. JSON endpoints live under `/v1/*`, and `/healthz` is the liveness
endpoint. The WebUI is read-only and has no built-in authentication; keep it on loopback or behind a
VPN/authenticating reverse proxy.

If this is also a development checkout, retain the development tools with `uv sync --extra dev
--extra web`.

### MCP

AtlasCore serves seven read-oriented tools over stdio. A generic MCP client entry looks like:

```json
{
  "mcpServers": {
    "atlascore": {
      "command": "/absolute/path/to/atlascore/.venv/bin/atlas",
      "args": ["mcp", "dev"],
      "cwd": "/absolute/path/to/atlascore"
    }
  }
}
```

The working directory matters because AtlasCore loads the checkout's `.env`. See the
[MCP tool contracts](mcp/README.md) for inputs, caps, provenance, auditing, and the raw-content gate.

## 8. Optional backends

The default configuration stays offline and deterministic. Install only what you use:

| Need | Install | Configure |
|---|---|---|
| Read-only WebUI/API | `uv sync --extra web` | Bind to loopback/VPN. |
| Local sentence-transformer embeddings and spaCy | `uv sync --extra local-embeddings` | Set the embedding/NER backend and follow the reindex runbook. |
| Cloud embeddings | `uv sync --extra cloud` | Set provider key, privacy posture, and spend cap. |
| Hosted summaries | `uv sync --extra api` | Set provider/model/key and spend cap. |
| Meilisearch client | `uv sync --extra search` | Adopt only after Postgres FTS is demonstrably insufficient. |

Changing embedding model or dimension requires the documented, bounded
[`atlas reindex-embeddings`](runbooks/README.md#11-embedding-backend-switch-backfill--reindex-r8)
procedure. Cloud backends may egress document text; read the
[security](security/README.md) and [compliance](compliance/README.md) boundaries first.

## 9. Verify a checkout

Useful local gates:

```bash
make test-unit
make security
make lint
make typecheck
make audit
make precommit
```

With PostgreSQL running:

```bash
make test-integration
make smoke
```

The protected GitHub workflow additionally verifies Python 3.12 and 3.13, package builds, source
manifests, secrets, migrations/schema drift, and PostgreSQL 16 + pgvector integration.

## 10. Prepare an unattended deployment

Before starting scheduler/worker services on a persistent host:

1. Use a pinned release checkout and an absolute persistent raw-store path.
2. Set `APP_ENV=prod` and replace all example credentials/contact values.
3. Configure exactly one delivery channel, an external heartbeat, a backup directory, and an offsite
   `rclone` or `rsync` destination.
4. Install `pg_dump` and `pg_restore` compatible with PostgreSQL 16.
5. Run the fail-closed gate, including real probes:

```bash
uv run atlas soak preflight --create-raw-dir --send-probes
```

Continue with the [deployment, backup, restore, recovery, and maintenance
runbooks](runbooks/README.md). A passing preflight proves configuration and connectivity; it does not
replace the planned month-long unattended soak.

## 11. Stop or reset the local stack

Stop PostgreSQL while preserving its named volume:

```bash
make down
```

`make clean` removes caches/build artifacts only. It deliberately keeps `.env`, `.venv`, raw data,
and database volumes. Deleting raw data or the Compose volume is destructive; use `atlas purge` for
audited erasure of ingested records and follow the restore runbook for recovery work.

## 12. Troubleshooting

| Symptom | Resolution |
|---|---|
| Docker client works but `make dev` hangs/fails | Start Docker Desktop/Engine and confirm `docker version` shows the server. |
| PostgreSQL authentication fails | Keep `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, and `DATABASE_URL` consistent; recreate only a disposable local volume if it was initialized with different credentials. |
| `database unreachable` | Run `make dev`, wait for the health check, then run `make migrate`. |
| Raw-store permission error | Use the local `./var/rawstore`, or create/chown the configured absolute directory. |
| Source is refused | Validate its YAML and policy; AtlasCore intentionally refuses disabled or unapproved sources. |
| Enqueued work does not become searchable | Ensure one worker is running; fetch creates parse/enrich work that the worker must drain. |
| No alerts/digest arrive | Configure one delivery channel, run `uv run atlas alerts run`, then use the delivery probes in `atlas soak preflight`. |
| Optional import is missing | Install the matching optional extra from section 8. |

For deeper diagnosis, use the [DLQ, stale-source, secrets, backup, and restore
runbooks](runbooks/README.md#15-runbook-index-summary).
