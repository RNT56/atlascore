"""``atlas alerts run`` — evaluate the alert rules and deliver new alerts (control-plane, B13).

Evaluates the MVP rules (`macro_release`/`cve_watch`/`sec_filing` + `freshness`) idempotently
(dedupe on `dedupe_key`) and delivers any not-yet-delivered alerts over the configured channel
(`ALERT_WEBHOOK_URL`/`NTFY_TOPIC`/SMTP, else stdout/log). MCP-independent (the control plane never
depends on MCP); `get_alerts` is the read-only MCP mirror.
"""

from __future__ import annotations

import typer
from sqlalchemy.exc import OperationalError

from atlascore.storage.postgres.base import make_engine
from atlascore.worker.alerts import run_alerts

alerts_app = typer.Typer(
    name="alerts",
    help="Evaluate alert rules and deliver alerts (control plane).",
    no_args_is_help=True,
    add_completion=False,
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
