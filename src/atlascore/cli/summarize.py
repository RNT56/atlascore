"""``atlas summarize`` — control-plane summary cache warm/read command."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

import typer

from atlascore.cli._db import session_or_exit
from atlascore.enrichment.summaries import get_summary_backend
from atlascore.enrichment.summaries.pipeline import summarize_documents


def summarize_cmd(
    document_ids: Annotated[
        list[str], typer.Argument(help="One or more document UUIDs; order is significant.")
    ],
    compute: Annotated[
        bool,
        typer.Option(
            "--compute/--cache-only",
            help="Compute a cache miss with the configured backend, or read cache only.",
        ),
    ] = True,
) -> None:
    """Return or pre-warm a cited summary through the canonical summary pipeline."""
    try:
        ids = [UUID(value) for value in document_ids]
    except ValueError as exc:
        typer.secho("one or more document IDs are not valid UUIDs", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc
    backend = get_summary_backend()
    with session_or_exit() as session, session.begin():
        outcome = summarize_documents(session, ids, backend=backend, allow_compute=compute)
    if not outcome.found or outcome.summary is None:
        typer.secho(outcome.reason or "summary unavailable", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)
    typer.secho(
        f"summary ({outcome.cache}; model={outcome.summary.model_name}/"
        f"{outcome.summary.model_version})",
        fg=typer.colors.CYAN,
        bold=True,
    )
    typer.echo(outcome.summary.summary_text)
    if outcome.summary.citations:
        typer.echo("\nCitations:")
        for document_id, claims in outcome.summary.citations.items():
            typer.echo(f"  {document_id}: {', '.join(claims)}")
