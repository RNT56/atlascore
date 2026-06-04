"""``atlas parse <raw_id>`` — (re)parse an immutable raw document into canonical documents.

Reads only the local raw store + DB (no network), so it needs no fetch-worker lock. Output
is reproducible (pinned ``parser_version`` against immutable raw) and idempotent
(last-write-wins upsert): re-running reports ``unchanged`` when nothing changed.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

import typer
from sqlalchemy.exc import OperationalError

from atlascore.core.clock import SystemClock
from atlascore.core.errors import ConfigError
from atlascore.storage.postgres.base import make_engine
from atlascore.storage.rawstore import RawStore
from atlascore.worker.parse import reparse_raw_document


def parse_raw(
    raw_id: Annotated[str, typer.Argument(help="The raw_document id (UUID) to (re)parse.")],
) -> None:
    """(Re)parse one raw document; create/update canonical document rows."""
    try:
        raw_uuid = UUID(raw_id)
    except ValueError as exc:
        typer.secho(f"not a valid raw_document id: {raw_id!r}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    engine = make_engine()
    try:
        result = reparse_raw_document(
            engine, raw_uuid, raw_store=RawStore.from_settings(), clock=SystemClock()
        )
    except ConfigError as exc:
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

    typer.secho(
        f"parsed raw {raw_id}: {result.documents_created} created, "
        f"{result.documents_updated} updated, {result.documents_unchanged} unchanged",
        fg=typer.colors.GREEN,
    )
