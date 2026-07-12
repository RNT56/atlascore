"""Operational preflight for the isolated unattended soak deployment."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text

from atlascore.alerts.delivery import LogChannel, get_delivery_channel
from atlascore.core.heartbeat import ping_heartbeat
from atlascore.core.settings import Settings, get_settings
from atlascore.storage.postgres.base import make_engine

soak_app = typer.Typer(
    help="Validate the isolated deployment before starting the unattended soak.",
    no_args_is_help=True,
    add_completion=False,
)


@dataclass(frozen=True)
class PreflightCheck:
    name: str
    ok: bool
    detail: str


def evaluate_soak_config(
    settings: Settings,
    *,
    environ: Mapping[str, str] | None = None,
    find_tool: Callable[[str], str | None] = shutil.which,
) -> list[PreflightCheck]:
    """Evaluate secret-free static readiness checks; no network or database I/O."""
    env = environ if environ is not None else os.environ
    database_safe = "change-me-local-only" not in settings.database_url
    user_agent_safe = (
        "you@example.com" not in settings.default_user_agent
        and "AtlasCore/0.0" not in settings.default_user_agent
        and "@" in settings.default_user_agent
    )
    channel = get_delivery_channel(settings)
    heartbeat = settings.heartbeat_url
    heartbeat_set = bool(heartbeat and heartbeat.get_secret_value().strip())
    offsite = bool(
        env.get("BACKUP_OFFSITE_REMOTE", "").strip() or env.get("BACKUP_OFFSITE_RSYNC", "").strip()
    )
    backup_dir = env.get("BACKUP_DIR", "").strip()
    checks = [
        PreflightCheck("environment", settings.app_env == "prod", "APP_ENV must be prod"),
        PreflightCheck("database credentials", database_safe, "placeholder password is forbidden"),
        PreflightCheck("user agent", user_agent_safe, "real version and contact are required"),
        PreflightCheck("delivery", not isinstance(channel, LogChannel), f"channel={channel.name}"),
        PreflightCheck("heartbeat", heartbeat_set, "external dead-man's-switch is required"),
        PreflightCheck(
            "raw store",
            settings.raw_store_dir.is_absolute() and settings.raw_store_dir.exists(),
            "absolute existing RAW_STORE_DIR required",
        ),
        PreflightCheck("pg_dump", find_tool("pg_dump") is not None, "tool must be on PATH"),
        PreflightCheck("pg_restore", find_tool("pg_restore") is not None, "tool must be on PATH"),
        PreflightCheck("backup directory", bool(backup_dir), "BACKUP_DIR is required"),
        PreflightCheck("offsite backup", offsite, "rclone or rsync destination is required"),
    ]
    if env.get("BACKUP_OFFSITE_REMOTE", "").strip():
        checks.append(
            PreflightCheck("rclone", find_tool("rclone") is not None, "tool must be on PATH")
        )
    if env.get("BACKUP_OFFSITE_RSYNC", "").strip():
        checks.append(
            PreflightCheck("rsync", find_tool("rsync") is not None, "tool must be on PATH")
        )
    return checks


def _migration_head() -> str:
    return str(ScriptDirectory.from_config(Config("alembic.ini")).get_current_head())


def _database_checks(settings: Settings) -> list[PreflightCheck]:
    engine = make_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            reachable = connection.scalar(text("SELECT 1")) == 1
            applied = connection.scalar(text("SELECT version_num FROM alembic_version"))
        head = _migration_head()
        return [
            PreflightCheck("database", reachable, "PostgreSQL connection and SELECT 1"),
            PreflightCheck("migrations", applied == head, f"database={applied}; code={head}"),
        ]
    except Exception as exc:
        return [PreflightCheck("database", False, f"unreachable ({type(exc).__name__})")]
    finally:
        engine.dispose()


@soak_app.command("preflight")
def preflight_cmd(
    probe_database: Annotated[bool, typer.Option("--probe-database/--no-probe-database")] = True,
    send_probes: Annotated[
        bool,
        typer.Option(
            "--send-probes",
            help="Send one real delivery message and heartbeat ping after static checks pass.",
        ),
    ] = False,
    create_raw_dir: Annotated[
        bool,
        typer.Option("--create-raw-dir", help="Create RAW_STORE_DIR before evaluating it."),
    ] = False,
) -> None:
    """Fail closed unless the host is ready for a meaningful unattended soak."""
    settings = get_settings()
    if create_raw_dir:
        Path(settings.raw_store_dir).mkdir(parents=True, exist_ok=True)
    checks = evaluate_soak_config(settings)
    if probe_database:
        checks.extend(_database_checks(settings))
    if send_probes and all(check.ok for check in checks):
        channel = get_delivery_channel(settings)
        try:
            status = channel.send(
                "AtlasCore soak preflight",
                "Delivery path verified before the unattended soak.",
            )
            checks.append(PreflightCheck("delivery probe", status == "sent", f"status={status}"))
        except Exception as exc:
            checks.append(PreflightCheck("delivery probe", False, f"failed ({type(exc).__name__})"))
        heartbeat_status = ping_heartbeat(settings)
        checks.append(
            PreflightCheck(
                "heartbeat probe", heartbeat_status == "pinged", f"status={heartbeat_status}"
            )
        )

    for check in checks:
        marker = "PASS" if check.ok else "FAIL"
        color = typer.colors.GREEN if check.ok else typer.colors.RED
        typer.secho(f"{marker:<4}  {check.name:<22} {check.detail}", fg=color)
    failures = [check for check in checks if not check.ok]
    if failures:
        typer.secho(f"preflight failed: {len(failures)} check(s)", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    typer.secho("preflight passed: host is ready to start the soak", fg=typer.colors.GREEN)
