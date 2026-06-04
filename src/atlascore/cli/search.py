"""``atlas search`` — full-text search over canonical documents (B6).

Read-only query path over the ``document`` FTS index (``ix_document_fts``, migration 0003).
Returns ranked, **sanitized** ``body_text`` snippets with provenance (source, canonical URL,
published time, document id) — never raw HTML (CLAUDE.md §13). Free-text input uses
``websearch`` syntax (``"quoted phrases"``, ``or``, ``-exclude``). Results are capped.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

import typer

from atlascore.cli._db import session_or_exit
from atlascore.storage.search import DEFAULT_LIMIT, MAX_LIMIT, SearchHit, search_documents

# Accepted --since/--until forms: a bare UTC date, or an ISO-ish datetime.
_DATE_FORMATS = ["%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"]


def _as_utc(value: datetime | None) -> datetime | None:
    """Treat a naive CLI date as UTC; normalize an aware one to UTC."""
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _print_hit(hit: SearchHit) -> None:
    typer.secho(hit.title or "(untitled)", fg=typer.colors.CYAN, bold=True)
    meta = f"  {hit.source_slug or hit.source_name}"
    if hit.published_at is not None:
        meta += f" · {hit.published_at.date().isoformat()}"
    meta += f" · id={hit.document_id} · rank {hit.rank:.3f}"
    typer.echo(meta)
    if hit.canonical_url:
        typer.echo(f"  {hit.canonical_url}")
    if hit.snippet:
        typer.echo(f"  {hit.snippet}")
    typer.echo("")


def search_cmd(
    query: Annotated[
        str, typer.Argument(help="Free-text query (websearch syntax: quotes, OR, -term).")
    ],
    source: Annotated[str | None, typer.Option("--source", help="Filter by source slug.")] = None,
    since: Annotated[
        datetime | None,
        typer.Option(
            "--since",
            formats=_DATE_FORMATS,
            help="Only documents published on/after this date (UTC).",
        ),
    ] = None,
    until: Annotated[
        datetime | None,
        typer.Option(
            "--until",
            formats=_DATE_FORMATS,
            help="Only documents published before this date (UTC).",
        ),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", "-n", min=1, max=MAX_LIMIT, clamp=True, help="Max results."),
    ] = DEFAULT_LIMIT,
) -> None:
    """Search documents by full text; print ranked, sanitized snippets with provenance."""
    with session_or_exit() as session:
        hits = search_documents(
            session,
            query,
            source=source,
            since=_as_utc(since),
            until=_as_utc(until),
            limit=limit,
        )
    if not hits:
        typer.echo("no matches")
        return
    for hit in hits:
        _print_hit(hit)
    typer.echo(f"{len(hits)} result(s)")
