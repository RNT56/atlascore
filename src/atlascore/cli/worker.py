"""``atlas worker run`` — the long-running fetch→parse→enrich drain loop (B19).

Drains the per-item pipeline via the existing ``run_one_*`` claimers with idle backoff. The fetch
role holds the single-fetch advisory lock (refuses a second fetch worker, R16); parse/enrich scale
out. Runs until SIGINT/SIGTERM. Control-plane, MCP-independent.
"""

from __future__ import annotations

import threading
from typing import Annotated

import typer
from sqlalchemy.exc import OperationalError

from atlascore.core.errors import FetchWorkerBusyError
from atlascore.storage.postgres.base import make_engine
from atlascore.worker.loop import ALL_ROLES, install_signal_handlers, run_worker_loop

worker_app = typer.Typer(
    name="worker",
    help="The continuous fetch/parse/enrich drain loop.",
    no_args_is_help=True,
    add_completion=False,
)


@worker_app.command(name="run")
def run(
    role: Annotated[
        list[str] | None,
        typer.Option(
            "--role", help="Roles to drain (repeatable): fetch|parse|enrich. Default all."
        ),
    ] = None,
) -> None:
    """Run the worker drain loop until SIGINT/SIGTERM."""
    roles = tuple(role) if role else ALL_ROLES
    invalid = set(roles) - set(ALL_ROLES)
    if invalid:
        typer.secho(f"unknown role(s): {sorted(invalid)}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    stop = threading.Event()
    install_signal_handlers(stop)
    engine = make_engine()
    try:
        typer.secho(f"worker: started roles={list(roles)} (Ctrl-C to stop)", fg=typer.colors.GREEN)
        run_worker_loop(engine, roles=roles, stop_event=stop)
    except FetchWorkerBusyError as exc:
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
    typer.secho("worker: stopped", fg=typer.colors.GREEN)
