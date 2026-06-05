"""``atlas health`` / ``atlas digest`` — the control-plane operational surface (B8).

Both are **CLI-driven and MCP-independent** (CLAUDE.md §17, architecture §7): they recompute
`source_health` (the freshness job), then read it + the `crawl_job` DLQ depth + a fetch-worker
liveness check. `atlas health` prints an on-demand snapshot; `atlas digest` builds the daily
summary and emits it over the delivery channel.

Delivery (B13): the digest text (`build_digest`) is emitted over the same configured channel as
alerts (`ALERT_WEBHOOK_URL` / `NTFY_TOPIC` / SMTP, else stdout/log) — `atlas digest` now closes the
B8 stdout-only seam. The digest is always echoed to stdout too (it is an operator-run command).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated

import typer
from sqlalchemy.orm import Session

from atlascore.alerts.delivery import get_delivery_channel
from atlascore.cli._db import session_or_exit
from atlascore.core.clock import SystemClock
from atlascore.core.logging import get_logger
from atlascore.scheduler.lock import scheduler_active
from atlascore.schemas.source_health import HealthStatus
from atlascore.storage.queue import dead_job_summary
from atlascore.storage.source_health import SourceHealthRow, list_source_health
from atlascore.worker.freshness import refresh_source_health
from atlascore.worker.run import fetch_worker_active

_log = get_logger("atlascore.digest")

# Statuses that are not `ok` — the ones a digest surfaces.
_PROBLEM_STATUSES = [s.value for s in HealthStatus if s is not HealthStatus.OK]


def _fmt_lag(lag: timedelta | None) -> str:
    if lag is None:
        return "—"
    total = int(lag.total_seconds())
    sign = "-" if total < 0 else ""
    total = abs(total)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{sign}{days}d{hours}h"
    if hours:
        return f"{sign}{hours}h{minutes}m"
    return f"{sign}{minutes}m"


def _refresh_then_read(
    session: Session, *, now: datetime, statuses: list[str] | None
) -> list[SourceHealthRow]:
    with session.begin():
        refresh_source_health(session, now=now)
    return list_source_health(session, statuses=statuses)


def build_digest(
    rows: list[SourceHealthRow],
    dlq: list[tuple[str, str, int]],
    *,
    fetch_worker_up: bool,
    scheduler_up: bool = False,
    now: datetime,
) -> str:
    """Render the daily digest text (pure; testable). ``rows`` are the non-ok sources."""
    lines = [f"AtlasCore daily digest — {now.isoformat(timespec='minutes')}", ""]
    if rows:
        lines.append(f"Sources needing attention ({len(rows)}):")
        for r in rows:
            lines.append(
                f"  [{r.status:<12}] {r.source_slug or r.source_name}"
                f"  lag={_fmt_lag(r.freshness_lag)}  fails={r.consecutive_failures}"
            )
    else:
        lines.append("Sources: all ok.")
    lines.append("")
    dlq_total = sum(count for _, _, count in dlq)
    lines.append(f"DLQ depth: {dlq_total}")
    for name, job_type, count in dlq:
        lines.append(f"  {count}x {job_type}  {name}")
    lines.append("")
    lines.append(
        f"Liveness: fetch worker {'present' if fetch_worker_up else 'NOT detected'}; "
        f"scheduler {'present' if scheduler_up else 'NOT detected'}."
    )
    return "\n".join(lines)


def deliver_digest(text: str) -> None:
    """Emit the digest over the configured channel (B13) and always echo to stdout.

    A delivery failure is logged but never crashes the operator command — the digest is still
    printed locally.
    """
    _log.info("daily_digest", lines=text.count("\n") + 1)
    channel = get_delivery_channel()
    try:
        status = channel.send("AtlasCore daily digest", text)
        _log.info("digest_delivered", channel=channel.name, status=status)
    except Exception as exc:  # delivery boundary: still show the digest locally
        _log.warning("digest_delivery_failed", channel=channel.name, error=str(exc))
    typer.echo(text)


def health_cmd(
    refresh: Annotated[
        bool, typer.Option("--refresh/--no-refresh", help="Recompute source_health first.")
    ] = True,
) -> None:
    """Print an on-demand health snapshot: source_health + DLQ depth + fetch-worker liveness."""
    now = SystemClock().now()
    with session_or_exit() as session:
        if refresh:
            with session.begin():
                refresh_source_health(session, now=now)
        rows = list_source_health(session)
        dlq = dead_job_summary(session)
        worker_up = fetch_worker_active(session)
        scheduler_up = scheduler_active(session)

    if not rows:
        typer.echo("no sources registered")
    for r in rows:
        color = typer.colors.GREEN if r.status == HealthStatus.OK.value else typer.colors.YELLOW
        typer.secho(
            f"{r.status:<12} {(r.source_slug or '-'):<24} lag={_fmt_lag(r.freshness_lag):<8}"
            f" fails={r.consecutive_failures}  items24h={r.item_count_recent or 0}  {r.source_name}",
            fg=color,
        )
    dlq_total = sum(count for _, _, count in dlq)
    typer.echo(f"\nDLQ depth: {dlq_total}")
    for name, job_type, count in dlq:
        typer.echo(f"  {count}x {job_type}  {name}")
    typer.echo(f"Fetch worker: {'present' if worker_up else 'NOT detected'}")
    typer.echo(f"Scheduler: {'present' if scheduler_up else 'NOT detected'}")


def digest_cmd() -> None:
    """Build the daily digest (non-ok sources + DLQ + liveness) and emit it via the channel."""
    now = SystemClock().now()
    with session_or_exit() as session:
        rows = _refresh_then_read(session, now=now, statuses=_PROBLEM_STATUSES)
        dlq = dead_job_summary(session)
        worker_up = fetch_worker_active(session)
        scheduler_up = scheduler_active(session)
    deliver_digest(
        build_digest(rows, dlq, fetch_worker_up=worker_up, scheduler_up=scheduler_up, now=now)
    )
