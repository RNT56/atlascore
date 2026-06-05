"""``atlas scheduler run`` — start the single-instance scheduler (B19).

Holds the scheduler advisory lock (a second instance is refused), enqueues due per-source fetch jobs
each tick, and runs freshness/alert/partition/audit housekeeping inline on their cadences. Runs
until SIGINT/SIGTERM. Control-plane, MCP-independent.
"""

from __future__ import annotations

import threading

import typer
from sqlalchemy.exc import OperationalError

from atlascore.core.errors import SchedulerBusyError
from atlascore.storage.postgres.base import make_engine
from atlascore.worker.loop import install_signal_handlers

scheduler_app = typer.Typer(
    name="scheduler",
    help="The single-instance scheduler (timed fetches + inline housekeeping).",
    no_args_is_help=True,
    add_completion=False,
)


@scheduler_app.command(name="run")
def run() -> None:
    """Run the scheduler until SIGINT/SIGTERM (single instance; refuses a second)."""
    from atlascore.scheduler.run import run_scheduler  # local import: keep CLI startup light

    stop = threading.Event()
    install_signal_handlers(stop)
    engine = make_engine()
    try:
        typer.secho("scheduler: started (Ctrl-C to stop)", fg=typer.colors.GREEN)
        run_scheduler(engine, stop_event=stop)
    except SchedulerBusyError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    except OperationalError as exc:
        typer.secho(
            f"database unreachable ({exc.__class__.__name__}); run `make dev && make migrate` first",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1) from exc
    finally:
        engine.dispose()
    typer.secho("scheduler: stopped", fg=typer.colors.GREEN)
