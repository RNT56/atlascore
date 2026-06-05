"""``atlas alerts run`` — evaluate the alert rules and deliver new alerts (control-plane, B13).

Evaluates the MVP rules (`macro_release`/`cve_watch`/`sec_filing` + `freshness`) idempotently
(dedupe on `dedupe_key`) and delivers any not-yet-delivered alerts over the configured channel
(`ALERT_WEBHOOK_URL`/`NTFY_TOPIC`/SMTP, else stdout/log). MCP-independent (the control plane never
depends on MCP); `get_alerts` is the read-only MCP mirror.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

import typer
from sqlalchemy.exc import OperationalError

from atlascore.cli._db import session_or_exit
from atlascore.core.errors import AlertTransitionError
from atlascore.storage.alerts import acknowledge_alert, list_alerts, resolve_alert
from atlascore.storage.postgres.base import make_engine
from atlascore.worker.alerts import run_alerts

alerts_app = typer.Typer(
    name="alerts",
    help="Evaluate alert rules and deliver alerts; lifecycle (control plane).",
    no_args_is_help=True,
    add_completion=False,
)


def _transition_cmd(alert_id: str, fn: object, verb: str) -> None:
    try:
        uuid = UUID(alert_id)
    except ValueError as exc:
        typer.secho(f"not a valid alert id: {alert_id!r}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    with session_or_exit() as session, session.begin():
        try:
            alert = fn(session, uuid)  # type: ignore[operator]
        except AlertTransitionError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc
        status = alert.status
    typer.secho(f"alert {alert_id} {verb} -> {status}", fg=typer.colors.GREEN)


@alerts_app.command(name="ack")
def ack(
    alert_id: Annotated[str, typer.Argument(help="The alert id (UUID) to acknowledge.")],
) -> None:
    """Acknowledge an alert (open -> acknowledged)."""
    _transition_cmd(alert_id, acknowledge_alert, "acknowledged")


@alerts_app.command(name="resolve")
def resolve(
    alert_id: Annotated[str, typer.Argument(help="The alert id (UUID) to resolve.")],
) -> None:
    """Resolve an alert (open|acknowledged -> resolved; refuses purged/terminal)."""
    _transition_cmd(alert_id, resolve_alert, "resolved")


@alerts_app.command(name="list")
def list_cmd(
    status: Annotated[
        list[str] | None, typer.Option("--status", help="Filter by status(es).")
    ] = None,
    alert_type: Annotated[
        list[str] | None, typer.Option("--type", help="Filter by alert type(s).")
    ] = None,
    min_severity: Annotated[
        str | None, typer.Option("--min-severity", help="info|low|medium|high|critical.")
    ] = None,
    since: Annotated[
        datetime | None, typer.Option("--since", help="Only alerts created on/after this UTC time.")
    ] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, max=200)] = 50,
) -> None:
    """List recent alerts (the same read path as the MCP get_alerts tool)."""
    with session_or_exit() as session:  # read-only
        rows = list_alerts(
            session,
            alert_types=alert_type,
            statuses=status,
            min_severity=min_severity,
            since=since,
            limit=limit,
        )
    if not rows:
        typer.echo("no alerts match")
        return
    for r in rows:
        typer.echo(
            f"{str(r.alert_id)[:8]}  {r.alert_type:<13} {r.severity:<8} {r.status:<12} "
            f"{r.created_at.date()}  {r.title}"
        )


@alerts_app.command(name="run")
def run() -> None:
    """Evaluate alert rules + deliver new alerts."""
    engine = make_engine()
    try:
        result = run_alerts(engine)
    except OperationalError as exc:
        typer.secho(
            f"database unreachable ({exc.__class__.__name__}); run `make dev && make migrate` first",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1) from exc
    finally:
        engine.dispose()

    typer.secho(
        f"alerts: {result.created} new, {result.delivered} delivered, {result.failed} failed",
        fg=typer.colors.GREEN,
    )
