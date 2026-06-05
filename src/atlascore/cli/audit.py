"""``atlas audit rotate`` — control-plane retention rotation (B16).

Deletes only ``retention_class='routine'`` audit_log rows older than the audit window (purge rows
are retained forever, R14) and prunes terminal (done|dead) crawl_job rows older than the crawl
window. Writes one retained bookkeeping entry per non-dry-run. MCP-independent.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated

import typer

from atlascore.cli._db import session_or_exit
from atlascore.core.clock import SystemClock
from atlascore.core.durations import parse_duration
from atlascore.core.settings import get_settings
from atlascore.storage.retention import run_retention

audit_app = typer.Typer(
    name="audit",
    help="Audit-log rotation + crawl_job pruning (control plane).",
    no_args_is_help=True,
    add_completion=False,
)


def _window(value: str, label: str) -> timedelta:
    # parse_duration raises ValueError on bad grammar and returns None for 'permanent'
    # (which is not a valid retention window — rotation needs a finite cutoff).
    try:
        parsed = parse_duration(value, allow_permanent=False, allow_hours=True)
    except ValueError:
        parsed = None
    if parsed is None:
        typer.secho(f"invalid {label} window: {value!r}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    return parsed


@audit_app.command(name="rotate")
def rotate(
    dry_run: Annotated[
        bool, typer.Option("--dry-run", "-n", help="Report what would be deleted; change nothing.")
    ] = False,
    audit_window: Annotated[
        str | None, typer.Option(help="Routine audit_log retention window (e.g. 180d).")
    ] = None,
    crawl_window: Annotated[
        str | None, typer.Option(help="Terminal crawl_job retention window (e.g. 90d).")
    ] = None,
) -> None:
    """Rotate routine audit_log rows + prune terminal crawl_job rows beyond their windows."""
    settings = get_settings()
    audit_td = _window(audit_window or settings.audit_routine_retention, "audit")
    crawl_td = _window(crawl_window or settings.crawl_job_retention, "crawl")

    with session_or_exit() as session, session.begin():
        result = run_retention(
            session,
            now=SystemClock().now(),
            audit_window=audit_td,
            crawl_window=crawl_td,
            dry_run=dry_run,
        )

    a, c = result.audit, result.crawl
    verb = "would delete" if dry_run else "deleted"
    color = typer.colors.YELLOW if dry_run else typer.colors.GREEN
    typer.secho(
        f"audit: {verb} {a.deleted} routine rows (cutoff {a.cutoff.date()}); "
        f"{a.purge_retained_under_cutoff} purge rows retained. "
        f"crawl_job: {verb} {c.deleted_done} done + {c.deleted_dead} dead (cutoff {c.cutoff.date()}).",
        fg=color,
    )
    if dry_run:
        typer.secho(
            "dry-run: nothing changed, no bookkeeping entry written.", fg=typer.colors.YELLOW
        )
