# AtlasCore soak LaunchAgents (macOS)

The macOS equivalent of [`infra/systemd/`](../systemd/) — for running the unattended soak on a
Mac mini (or any always-on Mac) before the eventual Linux/Hetzner deployment. Same invariants:
**exactly one scheduler** and **exactly one fetch worker** (`atlas scheduler run` / `atlas worker run`
hold pg advisory locks; a second of either is refused).

| Agent | Role | Schedule |
| --- | --- | --- |
| `com.atlascore.soak.scheduler` | timed fetches + inline housekeeping; pings `HEARTBEAT_URL` | always on (`KeepAlive`) |
| `com.atlascore.soak.worker` | drains fetch→parse→enrich (holds the fetch lock) | always on (`KeepAlive`) |
| `com.atlascore.soak.digest` | daily digest over the alert channel | `StartCalendarInterval` 08:00 local |
| `com.atlascore.soak.backup` | nightly `infra/backup.sh` (offsite if configured) | `StartCalendarInterval` 02:30 local |

The wrappers in `bin/` self-locate the soak root and `cd` there, so the `atlas` CLI auto-loads `./.env`
(pydantic). `soak-backup.sh` additionally loads `.env` into the environment (robust to spaces/parens —
it does **not** `. ./.env`), maps `POSTGRES_PASSWORD`→`PGPASSWORD`, sets `BACKUP_DIR`, and prepends
Homebrew paths so `pg_dump` (libpq) + `rclone` are found (launchd starts with a bare `PATH`).

## Isolation (running the soak alongside ongoing dev on one machine)

Run the soak from a **separate clone** pinned to a tag (e.g. `~/atlascore-soak` at `soak-YYYY-MM-DD`),
with its own `.env`, its own raw store (`RAW_STORE_DIR=<soak>/var/raw`), and the default Postgres on
`:5432`. Do day-to-day development in your normal checkout, and when you need integration tests there,
point them at a **throwaway** Postgres on a different port (`:5433`) so dev churn never touches the
soak's DB/volume. Don't `git pull` into the soak clone mid-month — that resets its "unattended" clock.

## Install

Prereqs in the soak clone: `cp .env.example .env` (fill in `POSTGRES_PASSWORD`, `DEFAULT_USER_AGENT`,
`NTFY_TOPIC`, `HEARTBEAT_URL`), `make setup`, `make dev && make migrate && make seed`, and for backups
`brew install libpq rclone`. Then:

```sh
# from the soak clone (SOAK_DIR defaults to the repo root containing this script):
infra/launchd/install.sh
```

Verify: `pgrep -af 'atlas scheduler run' | wc -l` (==1), same for the worker, and `atlas health`
(scheduler + fetch worker "present"). Logs land in `<soak>/var/log/`.

## Notes / gotchas

- **`StartCalendarInterval` uses the Mac's LOCAL timezone**, not UTC (unlike the systemd timers).
- **LaunchAgents run while the user is logged in.** On a Mac mini, enable auto-login (and disable
  sleep) so the agents resume after a reboot. For login-independent operation you'd promote these to
  `LaunchDaemons` (system, root) — overkill for a personal soak.
- **Don't sleep:** System Settings → Energy → prevent automatic sleeping; otherwise the soak stops.
- Tear down with `infra/launchd/uninstall.sh` (leaves the DB/volume; stop those with `make down`).
- This is a *trial* of the unattended machinery. The production target stays Linux + `infra/systemd/`.
