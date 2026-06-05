"""``atlas reindex-embeddings`` — bounded, resumable embedding backfill/reindex (B21, R8).

Re-embeds every ``document`` body through the configured embedding backend, in id-ordered batches
committed per batch (resumable: ``upsert_embeddings`` is idempotent and a no-op when the stored
``source_hash`` is unchanged, R13). The concrete realization of the runbook's embedding-dim-switch
step. Control-plane, MCP-independent.
"""

from __future__ import annotations

from typing import Annotated

import typer
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from atlascore.cli._db import session_or_exit
from atlascore.core.errors import ConfigError
from atlascore.core.logging import get_logger
from atlascore.core.settings import get_settings
from atlascore.enrichment.embeddings import get_embedding_backend
from atlascore.storage.embeddings import upsert_embeddings
from atlascore.storage.postgres.models import Document

_log = get_logger("atlascore.reindex")


def reindex_embeddings_cmd(
    model: Annotated[
        str | None, typer.Option("--model", help="Override EMBEDDING_MODEL for this run.")
    ] = None,
    batch: Annotated[
        int, typer.Option("--batch", min=1, max=1000, help="Commit every N docs.")
    ] = 100,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Report what would be re-embedded; write nothing."),
    ] = False,
) -> None:
    """Re-embed all documents through the configured backend (idempotent; resumable)."""
    settings = get_settings()
    if model:
        settings = settings.model_copy(update={"embedding_model": model})
    backend = get_embedding_backend(settings)

    with session_or_exit() as session:
        total = session.scalar(select(func.count()).select_from(Document)) or 0
        if dry_run:
            typer.secho(
                f"dry-run: would re-embed {total} documents via {backend.model_name}@"
                f"{backend.model_version} (dim {backend.dim}); nothing written.",
                fg=typer.colors.YELLOW,
            )
            return
        try:
            written = _run(session, backend, batch=batch)
        except ConfigError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc

    typer.secho(
        f"reindex complete: {total} documents, {written} chunk rows (re)written via "
        f"{backend.model_name}@{backend.model_version}.",
        fg=typer.colors.GREEN,
    )


def _run(session: Session, backend: object, *, batch: int) -> int:
    """Iterate documents id-ordered, re-embedding each; commit per batch. Returns rows written."""
    written = 0
    processed = 0
    doc_ids = list(session.scalars(select(Document.id).order_by(Document.id)))
    for doc_id in doc_ids:
        doc = session.get(Document, doc_id)
        if doc is None:
            continue
        written += upsert_embeddings(
            session,
            document_id=doc.id,
            source_hash=doc.content_hash,
            body_text=doc.body_text,
            backend=backend,  # type: ignore[arg-type]
        )
        processed += 1
        if processed % batch == 0:
            session.commit()
            _log.info("reindex_progress", processed=processed, total=len(doc_ids), rows=written)
    session.commit()
    return written
