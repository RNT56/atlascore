"""``atlas enrich <doc_id>`` — run the enrichment pipeline for one document.

Language → NER → topics → embeddings (CLAUDE.md §6). Reads only the DB + the configured backends
(no network), so it needs no fetch-worker lock. Idempotent: re-running a document with an unchanged
body re-computes the same topics/entities and skips re-embedding (R13).
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

import typer
from sqlalchemy.exc import OperationalError

from atlascore.core.errors import ConfigError
from atlascore.storage.postgres.base import make_engine
from atlascore.worker.enrich import enrich_document_by_id


def enrich_cmd(
    doc_id: Annotated[str, typer.Argument(help="The document id (UUID) to enrich.")],
) -> None:
    """Enrich one document: set language/topics/entity_ids, write mentions + embeddings."""
    try:
        doc_uuid = UUID(doc_id)
    except ValueError as exc:
        typer.secho(f"not a valid document id: {doc_id!r}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    engine = make_engine()
    try:
        result = enrich_document_by_id(engine, doc_uuid)
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
        f"enriched {doc_id}: language={result.language}, "
        f"topics={result.topics}, {result.entities} entities, "
        f"{result.mentions} mentions, {result.embeddings} embeddings",
        fg=typer.colors.GREEN,
    )
