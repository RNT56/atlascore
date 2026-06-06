# AtlasCore systemd units

Vetted unit + timer files for the unattended single-box deploy. These use the **real** console-script
entrypoints (`atlas scheduler run`, `atlas worker run`, `atlas digest`) — not `python -m apps.*`,
which does not exist. Together they enforce the load-bearing invariants: **exactly one scheduler** and
**exactly one fetch worker** (CLAUDE.md §17, R16). See `docs/runbooks/README.md` §2–§6C for the full
deploy walkthrough.

## Files

| Unit | Role | Cardinality |
| --- | --- | --- |
| `atlascore-stack.service` | Postgres 16 + pgvector via Docker Compose | one |
| `atlascore-scheduler.service` | timed fetches + inline housekeeping; pings `HEARTBEAT_URL` | **EXACTLY ONE** |
| `atlascore-worker.service` | drains fetch→parse→enrich; holds the fetch lock | **EXACTLY ONE fetch worker** |
| `atlascore-worker-proc@.service` | *optional* parse/enrich-only capacity (no fetch lock) | scale out |
| `atlascore-backup.service` + `.timer` | nightly `infra/backup.sh` (offsite) | one timer |
| `atlascore-digest.service` + `.timer` | daily digest over the alert channel | one timer |

## Assumptions (edit the units if your layout differs)

- Repo checked out at `/opt/atlascore`; venv at `/opt/atlascore/.venv` (`make setup`).
- Runtime env at `/opt/atlascore/.env` (`chmod 600`). `WorkingDirectory` is set so the compose file
  and relative paths resolve; `EnvironmentFile` also injects the vars into the process.
- A dedicated `atlascore` system user owns the repo, raw store, and backups. For
  `atlascore-stack.service`, that user must be in the `docker` group (or run the stack manually).

## Install

```bash
sudo cp infra/systemd/atlascore-*.service infra/systemd/atlascore-*.timer /etc/systemd/system/
sudo systemctl daemon-reload

# Stack first (or run `docker compose -f infra/docker-compose.yml up -d` yourself), then migrate+seed:
sudo systemctl enable --now atlascore-stack.service
#   ... make migrate && make seed ...

# The two long-lived daemons (ONE of each) + the periodic timers:
sudo systemctl enable --now atlascore-scheduler.service
sudo systemctl enable --now atlascore-worker.service
sudo systemctl enable --now atlascore-backup.timer
sudo systemctl enable --now atlascore-digest.timer

# Optional extra parse/enrich capacity (NEVER a second fetch worker):
# sudo systemctl enable --now atlascore-worker-proc@1.service
```

## Verify (post-deploy)

```bash
pgrep -af 'atlas scheduler run' | wc -l    # expect 1
pgrep -af 'atlas worker run'    | wc -l    # expect 1 (the fetch worker; +N proc workers if enabled)
atlas health                               # source_health + DLQ depth + scheduler/fetch-worker liveness
systemctl list-timers 'atlascore-*'        # backup + digest scheduled
```

## Dead-man's-switch (strongly recommended for unattended runs)

Set `HEARTBEAT_URL` in `.env` to an external uptime-monitor check (e.g. a healthchecks.io URL). The
scheduler GETs it after each successful tick, so if the box / Postgres / scheduler dies the pings
stop and the monitor **pages you**. Set the monitor's grace period a few ticks above
`SCHEDULER_TICK_SECONDS` (e.g. 5–10 min). Without it, the daily digest is the only failure signal and
a dead box produces silence, not an alert.
