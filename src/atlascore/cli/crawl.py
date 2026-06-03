"""``atlas crawl`` — enqueue crawl jobs and inspect the dead-letter queue (B3).

Enqueueing honors default-deny (only enabled + approved/restricted sources). The worker
loop that *runs* fetch jobs (and `atlas crawl once`) lands with the connector in B4.
"""

from __future__ import annotations

from typing import Annotated

import typer

from atlascore.cli._db import session_or_exit
from atlascore.core.clock import SystemClock
from atlascore.schemas.crawl_job import JobType
from atlascore.schemas.source import PolicyStatus
from atlascore.storage.queue import dead_jobs, enqueue
from atlascore.storage.sources import get_source

crawl_app = typer.Typer(
    name="crawl",
    help="Crawl jobs: enqueue a fetch job and inspect the dead-letter queue.",
    no_args_is_help=True,
)


@crawl_app.command("enqueue")
def enqueue_cmd(
    source_id: Annotated[str, typer.Argument(help="The source id (slug) or name.")],
) -> None:
    """Enqueue a fetch job for a registered, fetchable source (default-deny enforced)."""
    now = SystemClock().now()
    with session_or_exit() as session, session.begin():
        row = get_source(session, source_id)
        if row is None:
            typer.secho(
                f"source {source_id!r} is not registered (run: atlas source sync)",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=1)
        status = PolicyStatus(row.policy_status)
        if not (row.enabled and status.is_fetchable):
            reason = "disabled" if not row.enabled else f"policy={status.value}"
            typer.secho(
                f"REFUSED  {row.name}: {reason} (default-deny)",
                fg=typer.colors.YELLOW,
                err=True,
            )
            raise typer.Exit(code=1)
        slug = (row.config or {}).get("slug", str(row.id))
        dedupe_key = f"fetch:{slug}:manual:{now.isoformat()}"
        job = enqueue(
            session,
            source_id=row.id,
            job_type=JobType.FETCH,
            dedupe_key=dedupe_key,
            scheduled_at=now,
        )
        typer.secho(f"enqueued fetch job {job.id} for {row.name}", fg=typer.colors.GREEN)


@crawl_app.command("dlq")
def dlq_cmd(
    limit: Annotated[int, typer.Option("--limit", help="Max jobs to show.")] = 50,
) -> None:
    """List jobs in the dead-letter queue (`status=dead`)."""
    with session_or_exit() as session:
        jobs = dead_jobs(session, limit=limit)
        if not jobs:
            typer.echo("DLQ empty")
            return
        for job in jobs:
            typer.echo(
                f"{job.id}  {job.job_type:<9} attempt {job.attempt}/{job.max_attempts}"
                f"  {job.last_error or ''}"
            )
