"""``atlas tsp partitions`` — time-series partition management (B17, control-plane).

`status` lists existing ``time_series_point`` child partitions + per-child row counts + whether the
next-year partition exists; `ensure` idempotently creates the current..current+N yearly partitions
(DEFAULT catch-all retained). Read-only MCP surface is unchanged — `get_macro_series` reads across
partitions transparently.
"""

from __future__ import annotations

from typing import Annotated

import typer

from atlascore.cli._db import session_or_exit
from atlascore.core.clock import SystemClock
from atlascore.storage.partitions import (
    DEFAULT_PARTITION,
    ensure_yearly_partitions,
    needed_years,
    partition_for_year_exists,
    partition_row_counts,
)

tsp_app = typer.Typer(
    name="tsp",
    help="Time-series (time_series_point) partition management.",
    no_args_is_help=True,
    add_completion=False,
)
partitions_app = typer.Typer(no_args_is_help=True, add_completion=False)
tsp_app.add_typer(partitions_app, name="partitions")


@partitions_app.command(name="status")
def status() -> None:
    """List child partitions, per-child row counts, and whether next-year exists."""
    now = SystemClock().now()
    with session_or_exit() as session:
        counts = partition_row_counts(session)
        next_year = now.year + 1
        next_exists = partition_for_year_exists(session, next_year)
    if not counts:
        typer.echo("no partitions found (is the DB migrated?)")
    for name, n in sorted(counts.items()):
        marker = "  (DEFAULT catch-all)" if name == DEFAULT_PARTITION else ""
        typer.echo(f"{name:<32} {n:>10} rows{marker}")
    color = typer.colors.GREEN if next_exists else typer.colors.YELLOW
    typer.secho(
        f"next-year partition ({next_year}): {'present' if next_exists else 'MISSING — run ensure'}",
        fg=color,
    )


@partitions_app.command(name="ensure")
def ensure(
    years_ahead: Annotated[
        int, typer.Option("--years-ahead", min=0, max=10, help="Carve current..current+N years.")
    ] = 1,
) -> None:
    """Idempotently create the current..current+N yearly partitions (DEFAULT retained)."""
    now = SystemClock().now()
    with session_or_exit() as session, session.begin():
        result = ensure_yearly_partitions(session, now=now, years_ahead=years_ahead)
    if result.created:
        typer.secho(f"created partitions: {', '.join(result.created)}", fg=typer.colors.GREEN)
    else:
        years = ", ".join(str(y) for y in needed_years(now, years_ahead=years_ahead))
        typer.secho(f"all partitions present for {years} (nothing to do)", fg=typer.colors.GREEN)
