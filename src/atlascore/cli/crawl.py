"""``atlas crawl`` — enqueue/run crawl jobs and inspect the dead-letter queue (B3 + B4).

`enqueue` and `once` honor default-deny (only enabled + approved/restricted sources). `once`
holds the single-fetch-worker advisory lock while it runs the fetch (R16). The continuous worker
daemon is a later step; `once` is the one-shot operator path.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

import httpx
import typer
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from atlascore.cli._db import session_or_exit
from atlascore.connectors_sdk import SafeFetcher
from atlascore.core.clock import SystemClock
from atlascore.core.errors import FetchWorkerBusyError
from atlascore.core.settings import get_settings
from atlascore.schemas.crawl_job import JobType
from atlascore.schemas.source import PolicyStatus
from atlascore.storage.postgres.base import make_engine
from atlascore.storage.postgres.models import Source
from atlascore.storage.queue import dead_jobs, enqueue, requeue_dead
from atlascore.storage.rawstore import RawStore
from atlascore.storage.sources import get_source
from atlascore.worker.macro import MacroFetchResult
from atlascore.worker.run import FetchJobResult, fetch_worker_lock, run_one_fetch

crawl_app = typer.Typer(
    name="crawl",
    help="Crawl jobs: enqueue/run a fetch job and inspect the dead-letter queue.",
    no_args_is_help=True,
)


def _resolve_fetchable(session: Session, source_id: str) -> Source:
    """Return a registered, fetchable source or exit(1) (default-deny)."""
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
            f"REFUSED  {row.name}: {reason} (default-deny)", fg=typer.colors.YELLOW, err=True
        )
        raise typer.Exit(code=1)
    return row


def _slug(source: Source) -> str:
    return (source.config or {}).get("slug") or str(source.id)


@crawl_app.command("enqueue")
def enqueue_cmd(
    source_id: Annotated[str, typer.Argument(help="The source id (slug) or name.")],
) -> None:
    """Enqueue a fetch job for a registered, fetchable source (does not run it)."""
    now = SystemClock().now()
    with session_or_exit() as session, session.begin():
        source = _resolve_fetchable(session, source_id)
        dedupe_key = f"fetch:{_slug(source)}:manual:{now.isoformat()}"
        job = enqueue(
            session,
            source_id=source.id,
            job_type=JobType.FETCH,
            dedupe_key=dedupe_key,
            scheduled_at=now,
        )
        typer.secho(f"enqueued fetch job {job.id} for {source.name}", fg=typer.colors.GREEN)


@crawl_app.command("once")
def once_cmd(
    source_id: Annotated[str, typer.Argument(help="The source id (slug) or name.")],
) -> None:
    """Enqueue and RUN one fetch job (single-fetch-worker guarded): fetch + capture raw."""
    clock = SystemClock()
    settings = get_settings()
    engine = make_engine()
    try:
        with fetch_worker_lock(engine):
            with Session(engine) as session, session.begin():
                source = _resolve_fetchable(session, source_id)
                name = source.name
                enqueue(
                    session,
                    source_id=source.id,
                    job_type=JobType.FETCH,
                    dedupe_key=f"fetch:{_slug(source)}:once:{clock.now().isoformat()}",
                    scheduled_at=clock.now(),
                )
            with httpx.Client() as client:
                fetcher = SafeFetcher(
                    client=client, clock=clock, user_agent=settings.default_user_agent
                )
                result = run_one_fetch(
                    engine, fetcher=fetcher, raw_store=RawStore.from_settings(), clock=clock
                )
            _report_once(name, result)
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


def _report_once(name: str, result: FetchJobResult | MacroFetchResult | None) -> None:
    if result is None:
        typer.secho("no fetch job was available to run", fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(code=1)
    if result.error is not None:
        typer.secho(f"{name}: fetch failed — {result.error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    if result.not_modified:
        typer.secho(f"{name}: not modified (304) — no new raw", fg=typer.colors.GREEN)
        return
    if isinstance(result, MacroFetchResult):
        typer.secho(
            f"{name}: fetched; raw captured ({result.raw_document_id}); "
            f"{result.points_written} points upserted ({result.series_id})",
            fg=typer.colors.GREEN,
        )
        return
    typer.secho(
        f"{name}: fetched; raw captured ({result.raw_document_id}); "
        f"{result.new_entries} new / {result.total_entries} entries",
        fg=typer.colors.GREEN,
    )


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


@crawl_app.command("requeue")
def requeue_cmd(
    job_id: Annotated[str | None, typer.Argument(help="A specific dead job id to requeue.")] = None,
    all_dead: Annotated[
        bool, typer.Option("--all", help="Requeue ALL dead jobs (optionally filtered by --type).")
    ] = False,
    job_type: Annotated[
        str | None,
        typer.Option("--type", help="With --all: only this job_type (fetch|parse|enrich)."),
    ] = None,
    yes: Annotated[bool, typer.Option("--yes", help="Confirm a bulk --all requeue.")] = False,
) -> None:
    """Reset dead-letter (`status=dead`) jobs back to `pending` (replaces the break-glass SQL).

    Give a `<job_id>` to requeue one, or `--all [--type fetch]` to requeue the DLQ in bulk. Bulk
    requeue needs `--yes` (inspect first with `atlas crawl dlq`). Idempotent + replay-safe.
    """
    if not job_id and not all_dead:
        typer.secho(
            "specify a <job_id>, or --all [--type fetch] to requeue the DLQ in bulk",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    if all_dead and not yes:
        typer.secho(
            "refusing bulk requeue without --yes (review with `atlas crawl dlq` first)",
            fg=typer.colors.YELLOW,
            err=True,
        )
        raise typer.Exit(code=1)

    jid: UUID | None = None
    if job_id:
        try:
            jid = UUID(job_id)
        except ValueError as exc:
            typer.secho(f"invalid job id: {job_id}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=2) from exc

    now = SystemClock().now()
    with session_or_exit() as session, session.begin():
        count = requeue_dead(session, now=now, job_id=jid, job_type=job_type)
    typer.secho(f"requeued {count} job(s)", fg=typer.colors.GREEN)
