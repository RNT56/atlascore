"""``atlas backup`` — pg_dump + raw-store copy (CLAUDE.md §9/§12, runbook §4/§5).

A thin, cross-platform Python wrapper that takes a **local** backup: a compressed custom-format
``pg_dump`` of the control/normalized plane plus a copy of the immutable, content-addressed raw
store. Offsite sync (rclone/rsync) + retention + the systemd timer live in ``infra/backup.sh`` (the
operational path); this command is the convenient on-demand local snapshot the restore drill
(``infra/restore_drill.sh``) reads.

Secrets posture (§14): the DB password is passed to ``pg_dump`` via the ``PGPASSWORD`` environment
variable, **never** on the command line (so it can't leak into process listings or logs).
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated
from urllib.parse import unquote, urlparse

import typer

from atlascore.core.settings import get_settings


@dataclass(frozen=True)
class PgDumpPlan:
    """A ready-to-run ``pg_dump`` invocation: the argv and the env additions (password only)."""

    argv: list[str]
    env: dict[str, str]
    dbname: str


def build_pg_dump_plan(database_url: str, out_file: Path) -> PgDumpPlan:
    """Parse a SQLAlchemy DSN into a ``pg_dump`` argv (custom format) + a ``PGPASSWORD`` env entry.

    The password is returned in ``env`` (for ``PGPASSWORD``), never placed in ``argv`` (§14).
    """
    parsed = urlparse(database_url)
    if not parsed.path or parsed.path == "/":
        raise ValueError(f"DATABASE_URL has no database name: {database_url!r}")
    dbname = parsed.path.lstrip("/")
    argv = ["pg_dump", "--format=custom", "--compress=9", "--no-owner", "--no-privileges"]
    if parsed.hostname:
        argv += ["--host", parsed.hostname]
    if parsed.port:
        argv += ["--port", str(parsed.port)]
    if parsed.username:
        argv += ["--username", unquote(parsed.username)]
    argv += ["--dbname", dbname, "--file", str(out_file)]
    env = {"PGPASSWORD": unquote(parsed.password)} if parsed.password else {}
    return PgDumpPlan(argv=argv, env=env, dbname=dbname)


def backup_cmd(
    output_dir: Annotated[
        Path, typer.Option("--output-dir", "-o", help="Directory to write the backup into.")
    ] = Path("/var/backups/atlascore"),
    timestamp: Annotated[
        str | None,
        typer.Option(help="Override the UTC backup timestamp (mainly for tests/determinism)."),
    ] = None,
) -> None:
    """Write a local pg_dump + a copy of the raw store under ``output_dir/<timestamp>/``."""
    import os
    from datetime import UTC, datetime

    settings = get_settings()
    ts = timestamp or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dest = output_dir / ts
    dest.mkdir(parents=True, exist_ok=True)
    dump_file = dest / "atlascore-pg.dump"

    plan = build_pg_dump_plan(settings.database_url, dump_file)
    try:
        subprocess.run(plan.argv, env={**os.environ, **plan.env}, check=True)  # noqa: S603
    except FileNotFoundError as exc:
        typer.secho(
            "pg_dump not found on PATH; install postgresql-client (or use infra/backup.sh)",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1) from exc
    except subprocess.CalledProcessError as exc:
        typer.secho(f"pg_dump failed (exit {exc.returncode})", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    # Copy the immutable raw store (content-addressed, so a plain copy is a consistent snapshot).
    raw_src = settings.raw_store_dir
    raw_copied = 0
    if raw_src.exists():
        raw_dest = dest / "raw"
        shutil.copytree(raw_src, raw_dest, dirs_exist_ok=True)
        raw_copied = sum(1 for _ in raw_dest.rglob("*.gz"))

    typer.secho(
        f"backup written to {dest} (pg dump + {raw_copied} raw blobs). "
        "Offsite sync + retention: infra/backup.sh.",
        fg=typer.colors.GREEN,
    )
